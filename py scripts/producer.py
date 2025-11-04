import json
import random
import time
from datetime import datetime
from kafka import KafkaProducer

KAFKA_BROKER = "10.108.169.54:9092"   # din broker (Windows)
TOPIC = "traffic-data"

def weighted_speed():
    """Returner fart baseret på vægtet sandsynlighed."""
    r = random.random()  # 0.0–1.0
    if r < 0.20:
        return random.randint(0, 30)
    elif r < 0.40:
        return random.randint(30, 80)
    else:
        return random.randint(80, 90)

def make_fake_event():
    # vælg mellem rute 1, 2, 3
    route_id = random.choice([1, 2, 3])
    # vægtet hastighed
    speed = weighted_speed()

    # MySQL vil gerne have "YYYY-MM-DD HH:MM:SS"
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "speed": speed,
        "routeid": route_id,
        "timestamp": ts
    }

def main():
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8")
    )
    print(f"[producer] sending to {KAFKA_BROKER} topic={TOPIC}")
    while True:
        event = make_fake_event()
        producer.send(TOPIC, event)
        print("[producer] sent:", event)
        # send nyt event hvert sekund
        time.sleep(1)

if __name__ == "__main__":
    main()