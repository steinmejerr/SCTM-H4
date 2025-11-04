import sys
import json
import mysql.connector
from datetime import datetime
import pytz  # bruges kun til dansk tid

import paho.mqtt.client as mqtt  # <-- NYT

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col, from_json, to_timestamp,
    avg as _avg, max as _max, min as _min, count as _count
)
from pyspark.sql.types import (
    StructType, StructField, IntegerType, StringType
)

# -------------------------------------------------
# MQTT indstillinger
# -------------------------------------------------
MQTT_HOST = "10.108.169.80"
MQTT_PORT = 1883
MQTT_TOPIC_LIVE = "traffic/live"
MQTT_TOPIC_ANALYSIS = "traffic/analysis"


def mqtt_publish(topic, payload_dict):
    """Lille helper: åben, send, luk. Simpelt og robust for små batches."""
    try:
        client = mqtt.Client("spark-publisher")
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.publish(topic, json.dumps(payload_dict))
        client.disconnect()
    except Exception as e:
        # vi vil ikke vælte et helt Spark-batch fordi MQTT fejler
        print(f"[MQTT] fejl ved publish til {topic}: {e}")


# -------------------------------------------------
# Connection to kafka and docker topic
# -------------------------------------------------
BROKER = "10.108.169.54:9092"  # BROKERS IP
TOPIC = "traffic-data"         # DOCKER TOPIC

# JSON data that comes from the producer
schema = StructType([
    StructField("speed", IntegerType(), True),
    StructField("routeid", IntegerType(), True),
    StructField("timestamp", StringType(), True),
])

# MySQL connection info
MYSQL_HOST = "cpanel.teamzp.net"
MYSQL_DB = "rebootrp_sctm"
MYSQL_USER = "rebootrp_sctmuser"
MYSQL_PASS = "0P)^F*L5--!9N-s%"
MYSQL_URL = f"jdbc:mysql://{MYSQL_HOST}:3306/{MYSQL_DB}"
CARS_TABLE = "cars"
ANALYTIC_TABLE = "analytic_results"

# RouteIDs and Route names
ROUTE_NAMES = {
    1: "Ringstedvej",
    2: "Sorøvej",
    3: "Slagelsevej",
}

# ---------- Spark session ----------
spark = (
    SparkSession.builder
    .appName("KafkaToMySQLAnalytics")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

# ---------- læs fra Kafka ----------
raw_df = (spark.readStream
          .format("kafka")
          .option("kafka.bootstrap.servers", BROKER)
          .option("subscribe", TOPIC)
          .option("startingOffsets", "latest")
          .load())

json_df = raw_df.selectExpr("CAST(value AS STRING) AS json_str")

parsed_df = (json_df
             .select(from_json(col("json_str"), schema).alias("data"))
             .select("data.*"))

clean_df = (
    parsed_df
    .withColumn("timestamp", to_timestamp("timestamp", "yyyy-MM-dd HH:mm:ss"))
    .na.drop(subset=["speed", "routeid", "timestamp"])
)

# ---------- MYSQL HELP FUNCTIONS ----------

def truncate_cars():
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        database=MYSQL_DB,
        user=MYSQL_USER,
        password=MYSQL_PASS,
    )
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE cars;")
    conn.commit()
    cur.close()
    conn.close()

def get_last_analytics():
    """
    Henter sidste række fra analytic_results, så vi kan genbruge recent_congestion
    når avg_speed > 60
    """
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        database=MYSQL_DB,
        user=MYSQL_USER,
        password=MYSQL_PASS,
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT total_vehicles, max_speed, min_speed, recent_congestion
        FROM analytic_results
        ORDER BY resultid DESC
        LIMIT 1;
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return {
            "total_vehicles": 0,
            "max_speed": None,
            "min_speed": None,
            "recent_congestion": None,
        }
    return {
        "total_vehicles": int(row[0]),
        "max_speed": None if row[1] is None else int(row[1]),
        "min_speed": None if row[2] is None else int(row[2]),
        "recent_congestion": row[3],  # datetime-objekt
    }

# ---------- FOREACHBATCH FUNCTION (5 sek) ----------

def write_to_mysql_and_analytics(batch_df, batch_id):
    if batch_df.rdd.isEmpty():
        return

    # 1) skriv batch til cars
    (batch_df
        .select(
            col("speed").cast("int"),
            col("routeid").cast("int"),
            col("timestamp")
        )
        .write
        .format("jdbc")
        .option("url", MYSQL_URL)
        .option("dbtable", CARS_TABLE)
        .option("user", MYSQL_USER)
        .option("password", MYSQL_PASS)
        .option("driver", "com.mysql.cj.jdbc.Driver")
        .mode("append")
        .save()
    )

    # 2) læs hele cars for at lave analytics på DENNE batch
    cars_df = (spark.read
               .format("jdbc")
               .option("url", MYSQL_URL)
               .option("dbtable", CARS_TABLE)
               .option("user", MYSQL_USER)
               .option("password", MYSQL_PASS)
               .option("driver", "com.mysql.cj.jdbc.Driver")
               .load())

    if cars_df.rdd.isEmpty():
        return

    # seneste 5
    last5_df = (cars_df
                .orderBy(col("timestamp").desc())
                .limit(5))
    last5_rows = last5_df.collect()
    speeds_last5 = [r["speed"] for r in last5_rows if r["speed"] is not None]
    if not speeds_last5:
        return

    avg_last5 = int(sum(speeds_last5) / len(speeds_last5))

    if 0 <= avg_last5 < 30:
        congestion_text = "Kø"
    elif 30 <= avg_last5 <= 50:
        congestion_text = "Nedsat fart"
    else:
        congestion_text = "Flydende trafik"

    stats_row = (cars_df
                 .agg(
                     _avg("speed").alias("average_speed"),
                     _max("speed").alias("batch_max_speed"),
                     _min("speed").alias("batch_min_speed"),
                     _count("*").alias("batch_vehicles")
                 )
                 .collect()[0])

    def clamp_tinyint(v):
        if v is None:
            return 0
        v = int(v)
        return min(v, 90)

    average_speed = clamp_tinyint(stats_row["average_speed"])
    batch_max_speed = clamp_tinyint(stats_row["batch_max_speed"])
    batch_min_speed = clamp_tinyint(stats_row["batch_min_speed"])
    batch_vehicles = int(stats_row["batch_vehicles"])

    prev = get_last_analytics()
    previous_total = prev["total_vehicles"]
    previous_max = prev["max_speed"]
    previous_min = prev["min_speed"]
    previous_recent_congestion = prev["recent_congestion"]

    total_vehicles = previous_total + batch_vehicles
    final_max = batch_max_speed if previous_max is None else max(previous_max, batch_max_speed)
    final_min = batch_min_speed if previous_min is None else min(previous_min, batch_min_speed)

    # mest trafikerede rute
    routes_count_df = (cars_df
                       .groupBy("routeid")
                       .count()
                       .orderBy(col("count").desc()))
    routes = routes_count_df.collect()
    if routes:
        top_route_id = int(routes[0]["routeid"])
        recent_accident_prone_road = ROUTE_NAMES.get(top_route_id, f"Route {top_route_id}")
    else:
        recent_accident_prone_road = "Ukendt"

    # ---------- Dansk tid ----------
    dk_tz = pytz.timezone("Europe/Copenhagen")
    now_dk = datetime.now(dk_tz)
    now_dk_str = now_dk.strftime("%Y-%m-%d %H:%M:%S")

    if average_speed <= 60:
        recent_congestion_val = now_dk_str
    else:
        if previous_recent_congestion is not None:
            recent_congestion_val = previous_recent_congestion.strftime("%Y-%m-%d %H:%M:%S")
        else:
            recent_congestion_val = now_dk_str

    # skriv analytics til DB
    result_df = spark.createDataFrame(
        [
            (
                recent_congestion_val,
                int(average_speed),
                int(final_max),
                int(final_min),
                int(total_vehicles),
                recent_accident_prone_road
            )
        ],
        schema="""
            recent_congestion STRING,
            average_speed INT,
            max_speed INT,
            min_speed INT,
            total_vehicles INT,
            recent_accident_prone_road STRING
        """
    )

    (result_df
        .write
        .format("jdbc")
        .option("url", MYSQL_URL)
        .option("dbtable", ANALYTIC_TABLE)
        .option("user", MYSQL_USER)
        .option("password", MYSQL_PASS)
        .option("driver", "com.mysql.cj.jdbc.Driver")
        .mode("append")
        .save()
    )

    # ryd cars
    truncate_cars()

    # ---------- NYT: send analytics til MQTT ----------
    analysis_msg = {
        "recent_congestion": recent_congestion_val,
        "average_speed": int(average_speed),
        "max_speed": int(final_max),
        "min_speed": int(final_min),
        "total_vehicles": int(total_vehicles),
        "top_road": recent_accident_prone_road,
        "status": congestion_text,
        "ts": now_dk_str
    }
    mqtt_publish(MQTT_TOPIC_ANALYSIS, analysis_msg)

    print(
        f"[consumer] batch {batch_id} saved. "
        f"batch_vehicles={batch_vehicles} total_vehicles={total_vehicles} "
        f"max={final_max} min={final_min} status={congestion_text} "
        f"recent_congestion={recent_congestion_val}"
    )

# ---------- CUSTOM OUTPUT FUNKTION (1 sek) ----------

def print_speed_and_route(batch_df, batch_id):
    if batch_df.rdd.isEmpty():
        return
    rows = batch_df.select("speed", "routeid", "timestamp").collect()
    for r in rows:
        route_name = ROUTE_NAMES.get(r["routeid"], f"Ukendt ({r['routeid']})")
        print(f"[consumer] speed={r['speed']} route={route_name}")

        # ---------- NYT: send live til MQTT ----------
        live_msg = {
            "speed": int(r["speed"]),
            "routeid": int(r["routeid"]),
            "road": route_name,
            "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if r["timestamp"] else None
        }
        mqtt_publish(MQTT_TOPIC_LIVE, live_msg)

# ---------- STREAMS ----------

query = (clean_df
         .writeStream
         .outputMode("append")
         .foreachBatch(write_to_mysql_and_analytics)
         .trigger(processingTime="5 seconds")
         .start())

query_print = (clean_df
               .writeStream
               .outputMode("append")
               .foreachBatch(print_speed_and_route)
               .trigger(processingTime="1 second")
               .start())

print("[consumer] streaming ... Ctrl+C for stop")

spark.streams.awaitAnyTermination()
