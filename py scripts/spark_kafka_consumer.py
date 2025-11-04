import sys
import mysql.connector
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col, from_json, to_timestamp,
    avg as _avg, max as _max, min as _min, count as _count
)
from pyspark.sql.types import (
    StructType, StructField, IntegerType, BooleanType, StringType
)

# Kafka
BROKER = "10.108.169.54:9092"
TOPIC = "traffic-data"

# JSON vi får fra producer
schema = StructType([
    StructField("speed", IntegerType(), True),
    StructField("routeid", IntegerType(), True),
    StructField("directionForward", BooleanType(), True),
    StructField("timestamp", StringType(), True),
])

# MySQL info
MYSQL_HOST = "cpanel.teamzp.net"
MYSQL_DB = "rebootrp_sctm"
MYSQL_USER = "rebootrp_sctmuser"
MYSQL_PASS = "0P)^F*L5--!9N-s%"
MYSQL_URL = f"jdbc:mysql://{MYSQL_HOST}:3306/{MYSQL_DB}"
CARS_TABLE = "cars"
ANALYTIC_TABLE = "analytic_results"

ROUTE_NAMES = {
    1: "Ringstedvej",
    2: "Sorøvej",
    3: "Slagelsevej",
}

spark = (
    SparkSession.builder
    .appName("KafkaToMySQLAnalytics")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

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
    Hent seneste række fra analytic_results.
    Returnerer dict med keys:
      total_vehicles, max_speed, min_speed
    Hvis ingen rækker: returner 0, None, None
    """
    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        database=MYSQL_DB,
        user=MYSQL_USER,
        password=MYSQL_PASS,
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT total_vehicles, max_speed, min_speed
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
        }
    return {
        "total_vehicles": int(row[0]),
        "max_speed": None if row[1] is None else int(row[1]),
        "min_speed": None if row[2] is None else int(row[2]),
    }

def write_to_mysql_and_analytics(batch_df, batch_id):
    if batch_df.rdd.isEmpty():
        return

    # 1) skriv batch til cars
    (batch_df
        .select(
            col("speed").cast("int"),
            col("routeid").cast("int"),
            col("directionForward").cast("boolean"),
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

    # stats for DENNE batch (det der lige nu ligger i cars)
    stats_row = (cars_df
                 .agg(
                     _avg("speed").alias("average_speed"),
                     _max("speed").alias("batch_max_speed"),
                     _min("speed").alias("batch_min_speed"),
                     _count("*").alias("batch_vehicles")
                 )
                 .collect()[0])

    # clamp til TINYINT (du havde 90 som cap)
    def clamp_tinyint(v):
        if v is None:
            return 0
        v = int(v)
        return min(v, 90)

    average_speed = clamp_tinyint(stats_row["average_speed"])
    batch_max_speed = clamp_tinyint(stats_row["batch_max_speed"])
    batch_min_speed = clamp_tinyint(stats_row["batch_min_speed"])
    batch_vehicles = int(stats_row["batch_vehicles"])

    # Hent tidligere analytics (for at lave kumuleret total + min/max sammenligning)
    prev = get_last_analytics()
    previous_total = prev["total_vehicles"]
    previous_max = prev["max_speed"]
    previous_min = prev["min_speed"]

    # NYT total
    total_vehicles = previous_total + batch_vehicles

    # MAX: tag den højeste af tidligere og nuværende
    if previous_max is None:
        final_max = batch_max_speed
    else:
        final_max = max(previous_max, batch_max_speed)

    # MIN: tag den laveste af tidligere og nuværende
    if previous_min is None:
        final_min = batch_min_speed
    else:
        final_min = min(previous_min, batch_min_speed)

    # find mest belastede rute i denne batch
    routes_count_df = (cars_df
                       .groupBy("routeid")
                       .count()
                       .orderBy(col("count").desc()))
    routes = routes_count_df.collect()
    if routes:
        top_route_id = int(routes[0]["routeid"])
        most_accident_prone_road = ROUTE_NAMES.get(top_route_id, f"Route {top_route_id}")
    else:
        most_accident_prone_road = "Ukendt"

    # skriv analytics-række
    result_df = spark.createDataFrame(
        [
            (
                average_speed,
                final_max,
                final_min,
                total_vehicles,
                most_accident_prone_road
            )
        ],
        schema="""
            average_speed INT,
            max_speed INT,
            min_speed INT,
            total_vehicles INT,
            most_accident_prone_road STRING
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

    # tøm cars til næste batch
    truncate_cars()

    print(
        f"[consumer] batch {batch_id} saved. "
        f"batch_vehicles={batch_vehicles} total_vehicles={total_vehicles} "
        f"max={final_max} min={final_min} status={congestion_text}"
    )

query = (clean_df
         .writeStream
         .outputMode("append")
         .foreachBatch(write_to_mysql_and_analytics)
         .trigger(processingTime="5 seconds")
         .start())

print("[consumer] streaming ... Ctrl+C for stop")
query.awaitTermination()