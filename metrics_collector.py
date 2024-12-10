import asyncio
import psutil
import time
import redis
import json
import yaml
import requests
import logging
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configure Redis connection
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0

# Define metrics to collect
METRICS = ['cpu', 'memory', 'network', 'hdd', 'gpu', 'channels']

# Initialize Redis client
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
logger.info(f"Redis client initialized with host: {REDIS_HOST}, port: {REDIS_PORT}, db: {REDIS_DB}")

# Global variable to store the last network measurement
last_net_io = {}

def load_config():
    logger.info("Loading configuration")
    try:
        with open('/root/caricoder/config.yaml', 'r') as config_file:
            config = yaml.safe_load(config_file)
        logger.info("Configuration loaded successfully")
        return config
    except Exception as e:
        logger.error(f"Error loading configuration: {str(e)}")
        return {}

def get_total_channel_count():
    config = load_config()
    total_channels = len(config.get('channels', {}))
    logger.info(f"Total channel count: {total_channels}")
    return total_channels

def get_running_channel_count():
    logger.info("Getting running channel count")
    try:
        response = requests.get('http://localhost:8000/list')
        data = response.json()
        running_channels = len(data.get('channels', []))
        logger.info(f"Running channel count: {running_channels}")
        return running_channels
    except Exception as e:
        logger.error(f"Error getting running channel count: {str(e)}")
        return 0

def get_network_usage():
    global last_net_io
    current_time = time.time()
    current_net_io = psutil.net_io_counters(pernic=True)
    network_data = {}

    for interface, counters in current_net_io.items():
        if interface not in last_net_io:
            last_net_io[interface] = (counters, current_time)
            continue

        last_counters, last_time = last_net_io[interface]
        time_elapsed = current_time - last_time

        # Calculate rates
        bytes_sent = counters.bytes_sent - last_counters.bytes_sent
        bytes_recv = counters.bytes_recv - last_counters.bytes_recv
        send_rate = bytes_sent / time_elapsed
        recv_rate = bytes_recv / time_elapsed

        network_data[interface] = {
            'bytes_sent': bytes_sent,
            'bytes_recv': bytes_recv,
            'send_rate': send_rate,
            'recv_rate': recv_rate
        }

        # Update last measurement
        last_net_io[interface] = (counters, current_time)

    return network_data

async def collect_metrics():
    logger.info("Collecting system metrics")
    try:
        total_channels = get_total_channel_count()
        running_channels = get_running_channel_count()
        
        # Get CPU usage
        cpu_usage = psutil.cpu_percent(interval=1)
        logger.debug(f"CPU usage: {cpu_usage}%")
        
        # Get memory usage
        memory = psutil.virtual_memory()
        memory_usage = memory.percent
        logger.debug(f"Memory usage: {memory_usage}%")
        
        # Get network usage
        network_usage = get_network_usage()
        for interface, data in network_usage.items():
            logger.debug(f"Network usage for {interface}: "
                         f"Sent: {data['bytes_sent']} bytes ({data['send_rate']:.2f} B/s), "
                         f"Received: {data['bytes_recv']} bytes ({data['recv_rate']:.2f} B/s)")
        
        # Get disk usage
        disk = psutil.disk_usage('/')
        disk_usage = disk.percent
        logger.debug(f"Disk usage: {disk_usage}%")
        
        # Get GPU usage (placeholder, implement actual GPU monitoring if available)
        gpu_usage = 0
        logger.debug(f"GPU usage: {gpu_usage}%")
        
        metrics = {
            'cpu': cpu_usage,
            'memory': memory_usage,
            'network': network_usage,
            'hdd': disk_usage,
            'gpu': gpu_usage,
            'channels': f"{running_channels}/{total_channels}"
        }
        logger.info("System metrics collected successfully")
        return metrics
    except Exception as e:
        logger.error(f"Error collecting metrics: {str(e)}")
        return None

def store_live_data(metric_name, value):
    logger.debug(f"Storing live data for {metric_name}")
    timestamp = int(time.time())
    if metric_name == 'network':
        for interface, data in value.items():
            key = f"live:network:{interface}"
            interface_data = json.dumps({'timestamp': timestamp, 'value': data})
            redis_client.lpush(key, interface_data)
            redis_client.ltrim(key, 0, 59)  # Keep only last 5 minutes (60 data points)
    else:
        key = f"live:{metric_name}"
        data = json.dumps({'timestamp': timestamp, 'value': value})
        redis_client.lpush(key, data)
        redis_client.ltrim(key, 0, 59)
    logger.debug(f"Live data stored for {metric_name}")

def store_historic_data(metric_name, value):
    logger.debug(f"Storing historic data for {metric_name}")
    timestamp = int(time.time())
    if metric_name == 'network':
        for interface, data in value.items():
            key = f"historic:network:{interface}"
            interface_data = json.dumps({'timestamp': timestamp, 'value': data})
            redis_client.lpush(key, interface_data)
            redis_client.ltrim(key, 0, 287)  # Keep only last 24 hours (288 data points)
    else:
        key = f"historic:{metric_name}"
        data = json.dumps({'timestamp': timestamp, 'value': value})
        redis_client.lpush(key, data)
        redis_client.ltrim(key, 0, 287)
    logger.debug(f"Historic data stored for {metric_name}")

def calculate_average(metric_name):
    logger.debug(f"Calculating average for {metric_name}")
    if metric_name == 'network':
        # Handle network interfaces separately
        interfaces = redis_client.keys("live:network:*")
        averages = {}
        for interface_key in interfaces:
            interface = interface_key.decode().split(':')[-1]
            data = redis_client.lrange(interface_key, 0, -1)
            if not data:
                continue
            values = [json.loads(item)['value'] for item in data]
            avg_send_rate = sum(v['send_rate'] for v in values) / len(values)
            avg_recv_rate = sum(v['recv_rate'] for v in values) / len(values)
            averages[interface] = {'avg_send_rate': avg_send_rate, 'avg_recv_rate': avg_recv_rate}
        return averages
    else:
        key = f"live:{metric_name}"
        data = redis_client.lrange(key, 0, -1)
        if not data:
            logger.warning(f"No data found for {metric_name}")
            return None
        values = [json.loads(item)['value'] for item in data]
        if metric_name == 'channels':
            # For channels, we'll return the most recent value instead of an average
            logger.debug(f"Returning most recent value for channels: {values[0]}")
            return values[0]
        average = sum(values) / len(values)
        logger.debug(f"Average calculated for {metric_name}: {average}")
        return average

def get_live_data(metric_name):
    logger.debug(f"Getting live data for {metric_name}")
    if metric_name == 'network':
        interfaces = redis_client.keys("live:network:*")
        network_data = {}
        for interface_key in interfaces:
            interface = interface_key.decode().split(':')[-1]
            data = redis_client.lrange(interface_key, 0, -1)
            network_data[interface] = [json.loads(item) for item in data]
        return network_data
    else:
        key = f"live:{metric_name}"
        data = redis_client.lrange(key, 0, -1)
        logger.debug(f"Retrieved {len(data)} live data points for {metric_name}")
        return [json.loads(item) for item in data]

def get_historic_data(metric_name):
    logger.debug(f"Getting historic data for {metric_name}")
    if metric_name == 'network':
        interfaces = redis_client.keys("historic:network:*")
        network_data = {}
        for interface_key in interfaces:
            interface = interface_key.decode().split(':')[-1]
            data = redis_client.lrange(interface_key, 0, -1)
            network_data[interface] = [json.loads(item) for item in data]
        return network_data
    else:
        key = f"historic:{metric_name}"
        data = redis_client.lrange(key, 0, -1)
        logger.debug(f"Retrieved {len(data)} historic data points for {metric_name}")
        return [json.loads(item) for item in data]

async def metrics_collection_loop():
    logger.info("Starting metrics collection loop")
    while True:
        try:
            metrics = await collect_metrics()
            if metrics:
                for metric_name, value in metrics.items():
                    store_live_data(metric_name, value)
                    
                    # Store historic data every 5 minutes
                    if int(time.time()) % 300 == 0:
                        if metric_name == 'network':
                            avg_value = calculate_average(metric_name)
                            store_historic_data(metric_name, avg_value)
                        else:
                            avg_value = calculate_average(metric_name)
                            if avg_value is not None:
                                store_historic_data(metric_name, avg_value)
            
            logger.info("Metrics collected and stored successfully")
            await asyncio.sleep(5)  # Collect metrics every 5 seconds
        except Exception as e:
            logger.error(f"Error in metrics collection loop: {str(e)}")
            await asyncio.sleep(5)

async def main():
    logger.info("Starting metrics collection")
    try:
        await metrics_collection_loop()
    except KeyboardInterrupt:
        logger.info("Metrics collection stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error in main function: {str(e)}")

if __name__ == "__main__":
    logger.info("Metrics collector script started")
    asyncio.run(main())