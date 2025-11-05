import json # Imports JSON, to work with JSON data
import random # Imports random, to generate random numbers
import time # Imports time, to work with time-related functions
from datetime import datetime # Imports datetime, to work with date and time
from kafka import KafkaProducer # Imports KafkaProducer, to produce messages to Kafka

KAFKA_BROKER = "10.108.169.54:9092" # BROKER IP (WINDOWS LAPTOP)
TOPIC = "traffic-data" # Docker topic

def weighted_speed():
    r = random.random() 
    if r < 0.20: # 20% Chance
        return random.randint(0, 30)
    elif r < 0.40: # 40% Chance
        return random.randint(30, 80)
    else: # 40% Chance
        return random.randint(80, 90)

def make_fake_event():
    # Random rute, between 1, 2 and 3
    route_id = random.choice([1, 2, 3])
    # Calls the function weighted_speed to get the speed, of the cars
    speed = weighted_speed()

    # MySQL wants "YYYY-MM-DD HH:MM:SS"
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Returns the cars  speed, choosen routeid and timestamp
    return {
        "speed": speed,
        "routeid": route_id,
        "timestamp": ts
    }

def main():
    # Creates KafkaProducer from kafka-python library
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BROKER, # Tells which kafka-server the program should connect to
        value_serializer=lambda v: json.dumps(v).encode("utf-8") # Gets converted to json data and encoded to bytes
    )
    print(f"[producer] sending to {KAFKA_BROKER} topic={TOPIC}") # Prints a output in terminal, showing which broker and topic the program is sending to
    while True: # Starts en infinite loop
        event = make_fake_event() # Calls the function make_fake_event and stores the result in event
        producer.send(TOPIC, event) # Send the event data to the kafka-topic
        print("[producer] sent:", event) # Prints output in terminal, showing the data that we are sending
        # Sents on event every second
        time.sleep(1)

# Runs the main function when the script is executed
if __name__ == "__main__":
    main()