#!/usr/bin/env python3

import argparse
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Dict, Tuple
from collections import deque
import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import aiohttp
import yaml
import subprocess
import psutil
import time

def setup_logging(log_dir: str) -> logging.Logger:
    log_dir = os.path.abspath(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger("SchedulerService")
    logger.setLevel(logging.DEBUG)
    
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )
    
    main_log_path = os.path.join(log_dir, 'scheduler.log')
    file_handler = TimedRotatingFileHandler(
        main_log_path,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(detailed_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    
    logger.info(f"Logging initialized. Main log file: {main_log_path}")
    return logger

class Configuration:
    def __init__(self, config_path='/root/caricoder/config.yaml'):
        self.logger = logging.getLogger("SchedulerService")
        self.config_path = os.path.abspath(config_path)
        self.logger.info(f"Loading configuration from {self.config_path}")
        
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found at {self.config_path}")
            
        with open(self.config_path, 'r') as config_file:
            self.config = yaml.safe_load(config_file)
        self.channels = self.config.get('channels', {})
        self.logger.info(f"Loaded configuration for {len(self.channels)} channels")

    def get_input_type(self, channel: str) -> str:
        channel_config = self.channels.get(channel, {})
        inputs = channel_config.get('inputs', [])
        if inputs:
            input_type = inputs[0].get('type', '')
            self.logger.debug(f"Raw input type for channel {channel}: {input_type}")
            if 'srt' in input_type.lower():
                return 'srt_input'
            elif 'udp' in input_type.lower():
                return 'udp_input'
        self.logger.warning(f"Unknown input type for channel {channel}")
        return 'unknown'

    def get_channel_settings(self, channel: str) -> dict:
        settings = self.channels.get(channel, {})
        self.logger.debug(f"Retrieved settings for channel {channel}: {settings}")
        return settings

class QueueLogger:
    def __init__(self, log_dir: str, max_entries: int = 1000):
        self.logger = logging.getLogger("SchedulerService")
        self.log_dir = os.path.abspath(log_dir)
        self.max_entries = max_entries
        self.queue_log = deque(maxlen=max_entries)
        
        os.makedirs(self.log_dir, exist_ok=True)
        
        queue_log_path = os.path.join(self.log_dir, 'queue.log')
        handler = TimedRotatingFileHandler(
            queue_log_path,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding='utf-8'
        )
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        
        self.queue_logger = logging.getLogger("QueueLogger")
        self.queue_logger.setLevel(logging.INFO)
        self.queue_logger.addHandler(handler)
        self.queue_logger.propagate = False
        
        self.logger.info(f"Queue logger initialized. Log file: {queue_log_path}")

    def log_command(self, command: str):
        timestamp = datetime.now().isoformat()
        log_entry = f"{timestamp}: {command}"
        self.queue_log.append(log_entry)
        self.queue_logger.info(log_entry)

    def get_queue_state(self):
        return list(self.queue_log)

class Scheduler:
    def __init__(self, log_dir: str):
        self.logger = setup_logging(log_dir)
        self.log_dir = os.path.abspath(log_dir)
        
        os.makedirs(self.log_dir, exist_ok=True)
        self.crash_log_dir = os.path.join(self.log_dir, 'crash_logs')
        os.makedirs(self.crash_log_dir, exist_ok=True)
        
        self.processes: Dict[str, Tuple[asyncio.subprocess.Process, int]] = {}
        self.queue_logger = QueueLogger(self.log_dir)
        self.restart_attempts: Dict[str, Tuple[int, datetime]] = {}
        self.caricoder_path = os.path.abspath("/root/caricoder/caricoder.py")
        self.config = Configuration()
        self.stats_api_url = "http://localhost:5000"
        self.channel_stats = {}
        self.process_states = {}
        
        self.initial_settling_time = 20
        self.check_interval = 5
        self.packet_threshold = 100
        self.source_check_interval = 60
        self.channel_initializing = {}
        
        if not os.path.exists(self.caricoder_path):
            raise FileNotFoundError(f"CariCoder script not found at {self.caricoder_path}")
        
        self.logger.info(f"Scheduler initialized with log directory: {self.log_dir}")
        self.logger.info(f"CariCoder path: {self.caricoder_path}")

    async def start_caricoder(self, channel: str, source_index: int) -> asyncio.subprocess.Process:
        channel_log_dir = os.path.join(self.log_dir, channel)
        os.makedirs(channel_log_dir, exist_ok=True)
        
        command = [
            "python3",
            self.caricoder_path,
            channel,
            "--source-index",
            str(source_index),
            "--log-level",
            "DEBUG",
            "--log-dir",
            channel_log_dir
        ]
        
        self.logger.info(f"Starting CariCoder with command: {' '.join(command)}")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            self.logger.info(f"CariCoder process started for channel {channel} with PID {process.pid}")
            return process
        except Exception as e:
            self.logger.exception(f"Failed to start CariCoder process: {str(e)}")
            raise

    async def start_channel(self, channel: str, source_index: int):
        if channel in self.processes:
            self.logger.warning(f"Channel {channel} is already running")
            return False

        try:
            process = await self.start_caricoder(channel, source_index)
            self.processes[channel] = (process, source_index)
            self.channel_initializing[channel] = True
            asyncio.create_task(self.initialize_channel_monitoring(channel))
            self.logger.info(f"Successfully started channel {channel} with source index {source_index}")
            return True
        except Exception as e:
            self.logger.exception(f"Failed to start channel {channel}: {str(e)}")
            return False

    async def stop_channel(self, channel: str):
        self.logger.info(f"Attempting to stop channel: {channel}")
        if channel not in self.processes:
            self.logger.warning(f"Channel {channel} is not running")
            return False

        process, _ = self.processes[channel]
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
            self.logger.info(f"Channel {channel} stopped successfully")
        except asyncio.TimeoutError:
            self.logger.warning(f"Channel {channel} did not stop gracefully, forcing termination")
            process.kill()
        
        del self.processes[channel]
        self.logger.info(f"Channel {channel} removed from active processes")
        return True

    async def switch_source(self, channel: str, new_source_index: int):
        self.logger.info(f"Attempting to switch source for channel {channel} to index {new_source_index}")
        if channel not in self.processes:
            self.logger.warning(f"Channel {channel} is not running, cannot switch source")
            return False

        await self.stop_channel(channel)
        success = await self.start_channel(channel, new_source_index)
        if success:
            self.channel_stats[channel] = {
                'last_check': datetime.now(),
                'last_packet_count': 0,
                'check_count': 0,
                'healthy': True
            }
            self.channel_initializing[channel] = True
            asyncio.create_task(self.initialize_channel_monitoring(channel))
        return success

    async def fetch_latest_stats(self, channel: str, stat_type: str):
        async with aiohttp.ClientSession() as session:
            url = f"{self.stats_api_url}/stats/live/{channel}/{stat_type}"
            self.logger.debug(f"Fetching stats from: {url}")
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        self.logger.debug(f"Received stats for {channel}: {data[-1]}")
                        return data[-1]
                self.logger.warning(f"Failed to fetch stats for {channel}")
                return None

    #indent main
    async def monitor_processes(self):
        self.logger.info("Starting process monitor")
        
        while True:
            for channel in list(self.processes.keys()):
                try:
                    process, source_index = self.processes[channel]
                    
                    if process.returncode is not None:
                        timestamp = datetime.now().isoformat()
                        crash_log_path = os.path.join(
                            self.crash_log_dir, 
                            f"crash_{channel}_{timestamp.replace(':', '-')}.log"
                        )
                        
                        stdout, stderr = await process.communicate()
                        
                        try:
                            with open(crash_log_path, 'w') as f:
                                f.write("=== CariCoder Process Crash Report ===\n")
                                f.write(f"Timestamp: {timestamp}\n")
                                f.write(f"Channel: {channel}\n")
                                f.write(f"Process ID: {process.pid}\n")
                                f.write(f"Return Code: {process.returncode}\n")
                                f.write("\n=== Process Output ===\n")
                                f.write("--- STDOUT ---\n")
                                f.write(stdout.decode() if stdout else "No stdout output")
                                f.write("\n--- STDERR ---\n")
                                f.write(stderr.decode() if stderr else "No stderr output")
                        except Exception as write_error:
                            self.logger.error(f"Failed to write crash log: {str(write_error)}")
                        
                        self.logger.error(
                            f"Process crash detected:\n"
                            f"Channel: {channel}\n"
                            f"PID: {process.pid}\n"
                            f"Return Code: {process.returncode}\n"
                            f"Crash Log: {crash_log_path}"
                        )
                        
                        # Instead of restarting, just log the restart consideration
                        if self.should_restart(channel):
                            self.logger.warning(
                                f"Channel {channel} would normally be restarted here:\n"
                                f"- Current restart attempts: {self.restart_attempts.get(channel, (0, None))[0]}\n"
                                f"- Last attempt time: {self.restart_attempts.get(channel, (None, 'never'))[1]}\n"
                                f"- Source index that crashed: {source_index}"
                            )
                        else:
                            self.logger.warning(
                                f"Channel {channel} has exceeded maximum restart attempts:\n"
                                f"- Current restart attempts: {self.restart_attempts.get(channel, (0, None))[0]}\n"
                                f"- Last attempt time: {self.restart_attempts.get(channel, (None, 'never'))[1]}"
                            )
                        
                        # Remove the crashed process from the active processes
                        del self.processes[channel]
                    
                    await self.check_stream_health(channel)
                    
                except Exception as e:
                    self.logger.error(f"Error monitoring channel {channel}: {str(e)}")
            
            await asyncio.sleep(self.check_interval)

    async def check_stream_health(self, channel: str):
       if channel not in self.channel_stats or self.channel_initializing.get(channel, False):
           self.logger.debug(f"Skipping health check for channel {channel} - not initialized or still initializing")
           return

       stats = self.channel_stats[channel]
       current_time = datetime.now()

       if current_time - stats['last_check'] < timedelta(seconds=self.check_interval):
           return

       input_type = self.config.get_input_type(channel)
       self.logger.info(f"Checking stream health for channel {channel} with input type {input_type}")
       latest_stats = await self.fetch_latest_stats(channel, input_type)
       if not latest_stats:
           self.logger.warning(f"Failed to fetch stats for channel {channel}")
           return

       current_packets = latest_stats['stats'].get('packets-received', 0)
       packet_increase = current_packets - stats['last_packet_count']

       self.logger.info(f"Channel {channel} - Current packets: {current_packets}, "
                     f"Increase: {packet_increase}, Check count: {stats['check_count']}")

       if packet_increase >= self.packet_threshold:
           stats['healthy'] = True
           stats['check_count'] = 0
           self.logger.info(f"Channel {channel} is healthy. Packets received: {packet_increase}")
       else:
           stats['check_count'] += 1
           if stats['check_count'] >= 3:
               self.logger.warning(f"Channel {channel} may be down. Packets received: {packet_increase}")
               stats['healthy'] = False
               await self.handle_stream_failure(channel)
           else:
               self.logger.warning(f"Channel {channel} packet increase below threshold. "
                               f"Check {stats['check_count']}/3")

       stats['last_check'] = current_time
       stats['last_packet_count'] = current_packets

    async def handle_stream_failure(self, channel: str):
       channel_settings = self.config.get_channel_settings(channel)
       inputs = channel_settings.get('inputs', [])
       current_index = self.processes[channel][1]
       
       next_source_index = None
       highest_priority = -1
       for i, input_source in enumerate(inputs):
           if i != current_index and input_source.get('priority', 0) > highest_priority:
               if await self.test_source(input_source.get('uri')):
                   next_source_index = i
                   highest_priority = input_source.get('priority', 0)
       
       if next_source_index is not None:
           self.logger.info(f"Switching to backup source {next_source_index} for channel {channel}")
           await self.switch_source(channel, next_source_index)
       else:
           self.logger.warning(f"No available backup sources for channel {channel}")

    def should_restart(self, channel: str) -> bool:
       now = datetime.now()
       if channel not in self.restart_attempts:
           self.restart_attempts[channel] = (1, now)
           self.logger.info(f"First restart attempt for channel {channel}")
           return True
       
       attempts, last_attempt = self.restart_attempts[channel]
       if (now - last_attempt) > timedelta(minutes=10):
           self.restart_attempts[channel] = (1, now)
           self.logger.info(f"Resetting restart attempts for channel {channel} after 10 minutes")
           return True
       
       if attempts < 5:
           self.restart_attempts[channel] = (attempts + 1, now)
           self.logger.info(f"Restart attempt {attempts + 1} for channel {channel}")
           return True
       
       self.logger.warning(f"Maximum restart attempts reached for channel {channel}")
       return False

    async def initialize_channel_monitoring(self, channel: str):
       self.logger.info(f"Waiting {self.initial_settling_time} seconds for channel {channel} to settle")
       await asyncio.sleep(self.initial_settling_time)
       
       self.channel_stats[channel] = {
           'last_check': datetime.now(),
           'last_packet_count': 0,
           'check_count': 0,
           'healthy': True
       }
       self.logger.info(f"Initialized monitoring for channel {channel}")
       self.channel_initializing[channel] = False

    async def test_source(self, source_url: str) -> bool:
       self.logger.info(f"Testing source: {source_url}")
       try:
           command = [
               "ffprobe",
               "-v", "error",
               "-show_entries", "stream=codec_type",
               "-of", "json",
               "-i", source_url
           ]
           
           process = await asyncio.create_subprocess_exec(
               *command,
               stdout=asyncio.subprocess.PIPE,
               stderr=asyncio.subprocess.PIPE
           )
           
           stdout, stderr = await process.communicate()
           
           if process.returncode == 0:
               self.logger.info(f"Source {source_url} is available")
               return True
           else:
               self.logger.warning(f"Source {source_url} is not available. Error: {stderr.decode()}")
               return False
       
       except Exception as e:
           self.logger.error(f"Error testing source {source_url}: {str(e)}")
           return False

    async def check_all_sources(self):
       self.logger.info("Checking sources for channels not on highest priority")
       channels = list(self.processes.keys())
       for channel in channels:
           if channel not in self.processes:
               continue
           _, current_index = self.processes[channel]
           channel_settings = self.config.get_channel_settings(channel)
           inputs = channel_settings.get('inputs', [])
       
           current_priority = inputs[current_index].get('priority', 0)
           highest_priority = max(input.get('priority', 0) for input in inputs)
       
           if current_priority >= highest_priority:
               self.logger.debug(f"Channel {channel} already on highest priority source. Skipping check.")
               continue
       
           for i, input_source in enumerate(inputs):
               if i == current_index:
                   continue
           
               if input_source.get('priority', 0) <= current_priority:
                   continue
           
               source_url = input_source.get('uri')
               if await self.test_source(source_url):
                   self.logger.info(f"Higher priority source {source_url} for channel {channel} is available")
                   self.logger.info(f"Switching to higher priority source for channel {channel}")
                   await self.switch_source(channel, i)
                   break
               else:
                   self.logger.info(f"Higher priority source {source_url} for channel {channel} is not available")

    async def periodic_source_check(self):
       while True:
           await self.check_all_sources()
           await asyncio.sleep(self.source_check_interval)

class ChannelRequest(BaseModel):
   channel: str
   source_index: int

app = FastAPI()

app.add_middleware(
   CORSMiddleware,
   allow_origins=["*"],
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)

scheduler: Scheduler = None

@app.on_event("startup")
async def startup_event():
   global scheduler
   parser = argparse.ArgumentParser(description="CariCoder Scheduler Service")
   parser.add_argument('--log-dir', default="/var/log/caricoder_scheduler",
                      help="Directory for storing logs")
   args = parser.parse_args()
   
   log_dir = os.path.abspath(args.log_dir)
   
   scheduler = Scheduler(log_dir)
   asyncio.create_task(scheduler.monitor_processes())
   asyncio.create_task(scheduler.periodic_source_check())

@app.post("/start")
async def start_channel(request: ChannelRequest):
   logger = logging.getLogger("SchedulerService")
   logger.info(f"Received request to start channel: {request.channel}")
   success = await scheduler.start_channel(request.channel, request.source_index)
   if success:
       logger.info(f"Successfully started channel {request.channel}")
       return {"message": f"Started channel {request.channel}"}
   else:
       logger.error(f"Failed to start channel {request.channel}")
       raise HTTPException(status_code=400, detail=f"Failed to start channel {request.channel}")

@app.post("/stop")
async def stop_channel(request: ChannelRequest):
   logger = logging.getLogger("SchedulerService")
   logger.info(f"Received request to stop channel: {request.channel}")
   success = await scheduler.stop_channel(request.channel)
   if success:
       logger.info(f"Successfully stopped channel {request.channel}")
       return {"message": f"Stopped channel {request.channel}"}
   else:
       logger.error(f"Failed to stop channel {request.channel}")
       raise HTTPException(status_code=400, detail=f"Failed to stop channel {request.channel}")

@app.post("/switch")
async def switch_source(request: ChannelRequest):
   logger = logging.getLogger("SchedulerService")
   logger.info(f"Received request to switch source for channel: {request.channel}")
   success = await scheduler.switch_source(request.channel, request.source_index)
   if success:
       logger.info(f"Successfully switched channel {request.channel} to source index {request.source_index}")
       return {"message": f"Switched channel {request.channel} to source index {request.source_index}"}
   else:
       logger.error(f"Failed to switch channel {request.channel}")
       raise HTTPException(status_code=400, detail=f"Failed to switch channel {request.channel}")

@app.post("/restart")
async def restart_channel(request: ChannelRequest):
   logger = logging.getLogger("SchedulerService")
   logger.info(f"Received request to restart channel: {request.channel}")
   await scheduler.stop_channel(request.channel)
   success = await scheduler.start_channel(request.channel, request.source_index)
   if success:
       logger.info(f"Successfully restarted channel {request.channel}")
       return {"message": f"Restarted channel {request.channel}"}
   else:
       logger.error(f"Failed to restart channel {request.channel}")
       raise HTTPException(status_code=400, detail=f"Failed to restart channel {request.channel}")

@app.get("/list")
async def list_channels():
   logger = logging.getLogger("SchedulerService")
   logger.info("Received request to list all running channels")
   channels = []
   
   cpu_count = psutil.cpu_count()
   
   for channel, (process, source_index) in scheduler.processes.items():
       try:
           pid = process.pid
           if pid:
               try:
                   p = psutil.Process(pid)
                   uptime_seconds = int(time.time() - p.create_time())
                   hours = uptime_seconds // 3600
                   minutes = (uptime_seconds % 3600) // 60
                   seconds = uptime_seconds % 60
                   uptime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                   
                   cpu_percent = p.cpu_percent(interval=0.1)
                   per_core_usage = cpu_percent / cpu_count
                   
                   channels.append({
                       "channel": channel,
                       "source_index": source_index,
                       "pid": pid,
                       "status": "running",
                       "uptime": uptime,
                       "cpu_usage_total": f"{cpu_percent:.1f}%",
                       "cpu_usage_per_core": f"{per_core_usage:.1f}%",
                       "cpu_cores": cpu_count
                   })
               except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                   logger.error(f"Process error for channel {channel}: {str(e)}")
                   channels.append({
                       "channel": channel,
                       "source_index": source_index,
                       "pid": pid,
                       "status": "error"
                   })
           else:
               channels.append({
                   "channel": channel,
                   "source_index": source_index,
                   "pid": None,
                   "status": "error"
               })
       except Exception as e:
           logger.error(f"Error getting process info for channel {channel}: {str(e)}")
           channels.append({
               "channel": channel,
               "source_index": source_index,
               "pid": None,
               "status": "error"
           })

   logger.info(f"Returning list of {len(channels)} channels")
   return {"channels": channels}

@app.get("/queue")
async def get_queue_state():
   logger = logging.getLogger("SchedulerService")
   logger.info("Received request to get current queue state")
   queue_state = scheduler.queue_logger.get_queue_state()
   logger.info(f"Returning queue state with {len(queue_state)} entries")
   return {"queue_state": queue_state}

if __name__ == "__main__":
   uvicorn.run(app, host="0.0.0.0", port=8000)