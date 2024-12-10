import time
import json
import logging

logger = logging.getLogger(__name__)

class StatsCollector:
    def __init__(self, channel_name, redis_client):
        self.channel_name = channel_name
        self.redis_client = redis_client
        self.last_aggregation_time = 0  # Initialize to 0

    def add_stats(self, stat_type, stats):
        timestamp = int(time.time())
        
        # Store live stats in Redis
        self.redis_client.zadd(f"channel:{self.channel_name}:{stat_type}:live", {json.dumps(stats): timestamp})
        
        # Trim live stats to keep only the last 5 minutes
        self.redis_client.zremrangebyscore(f"channel:{self.channel_name}:{stat_type}:live", 0, timestamp - 300)
        
        # Check if it's time to aggregate historic stats (every 5 minutes)
        if timestamp - self.last_aggregation_time >= 300:
            logger.info(f"Triggering aggregation at {timestamp}")
            self._aggregate_historic_stats(stat_type, timestamp)
            self.last_aggregation_time = timestamp

    def _aggregate_historic_stats(self, stat_type, timestamp):
        logger.info(f"Aggregating historic stats for {self.channel_name}, stat_type: {stat_type}, timestamp: {timestamp}")
        start_time = timestamp - 300
        live_stats = self.redis_client.zrangebyscore(f"channel:{self.channel_name}:{stat_type}:live", start_time, timestamp)
    
        logger.info(f"Found {len(live_stats)} live stats to aggregate")
    
        if live_stats:
            avg_stats = self._calculate_average_stats(live_stats)
            self.redis_client.zadd(f"channel:{self.channel_name}:{stat_type}:historic", {json.dumps(avg_stats): timestamp})
            logger.info(f"Stored aggregated historic stats: {json.dumps(avg_stats)}")
        
            # Keep only 3 hours of historic data
            removed = self.redis_client.zremrangebyscore(f"channel:{self.channel_name}:{stat_type}:historic", 0, timestamp - 10800)
            logger.info(f"Removed {removed} old historic data points")
        else:
            logger.warning("No live stats found to aggregate")


    def _calculate_average_stats(self, stats_list):
        total_stats = {}
        count = len(stats_list)
        
        for stats_json in stats_list:
            stats = json.loads(stats_json)
            for key, value in stats.items():
                if isinstance(value, (int, float)):
                    total_stats[key] = total_stats.get(key, 0) + value

        return {key: value / count for key, value in total_stats.items()}

    #def get_live_stats(self):
        #return list(self.redis_client.zrange(f"channel:{self.channel_name}:srtinput:live", 0, -1, withscores=True))
    def get_live_stats(self, stat_type):
        return list(self.redis_client.zrange(f"channel:{self.channel_name}:{stat_type}:live", 0, -1, withscores=True))

    #def get_historic_stats(self):
        #return list(self.redis_client.zrange(f"channel:{self.channel_name}:srtinput:historic", 0, -1, withscores=True))
    def get_historic_stats(self, stat_type):
        return list(self.redis_client.zrange(f"channel:{self.channel_name}:{stat_type}:historic", 0, -1, withscores=True))