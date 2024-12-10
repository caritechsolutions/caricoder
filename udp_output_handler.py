#!/usr/bin/env python3

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
from config import Configuration
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import redis
import json
import sys
import os
import time
import argparse
from datetime import datetime
from pathlib import Path
from stats_collector import StatsCollector

def setup_logging(channel_name, output_index, log_dir='logs', log_level='INFO'):
    """Configure logging with both console and file outputs"""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'udp_output_{channel_name}_{output_index}_{timestamp}.log')
    
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - [%(levelname)s] - %(message)s\n'
        'Thread: %(threadName)s - Process: %(process)d\n'
        '%(pathname)s:%(lineno)d\n'
    )
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    while root_logger.handlers:
        root_logger.removeHandler(root_logger.handlers[0])
    
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(getattr(logging, log_level))
    
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    return logging.getLogger(__name__)

def check_full_passthrough(config, channel_name):
    """Check if both audio and video are set to passthrough"""
    transcoding = config.get_channel_settings(channel_name).get('transcoding', {})
    
    video_streams = transcoding.get('video', {}).get('streams', [])
    if not video_streams:
        video_passthrough = transcoding.get('video', {}).get('codec') == 'passthrough'
    else:
        video_passthrough = all(stream.get('codec') == 'passthrough' for stream in video_streams)
    
    audio_passthrough = transcoding.get('audio', {}).get('codec') == 'passthrough'
    
    return video_passthrough and audio_passthrough

class UDPOutputWatchdog:
    """Watchdog system for UDP output pipeline monitoring"""
    def __init__(self, pipeline, logger, handler):
        self.pipeline = pipeline
        self.logger = logger
        self.handler = handler
        self.restart_attempts = 0
        self.MAX_RESTART_ATTEMPTS = 30
        self.RESTART_DELAY = 5  # seconds
        self.handling_failure = False
        
        # Set default timeout values (in milliseconds)
        self.timeouts = {
            'shmsrc': 5000,  # Input timeout
            'queue1': 5000,   # Queue timeout
            'udpsink': 5000  # Output timeout
        }
        
        self.last_activity = {}
        self.watchdog_timers = {}

    def setup_watchdog(self):
        """Initialize watchdog monitoring for pipeline elements"""
        try:
            elements_to_monitor = ['udpsink']
            
            for element_name in elements_to_monitor:
                element = self.pipeline.get_by_name(element_name)
                if not element:
                    self.logger.error(f"Could not find element {element_name}")
                    continue
                    
                # Add probe to source pad
                pad = element.get_static_pad("src")
                if pad:
                    pad.add_probe(
                        Gst.PadProbeType.BUFFER,
                        self._probe_callback,
                        element_name
                    )
                    
                    # Start watchdog timer
                    timer_id = GLib.timeout_add(
                        self.timeouts[element_name],
                        self._watchdog_timeout_callback,
                        element_name
                    )
                    self.watchdog_timers[element_name] = timer_id
                    
            self.logger.info("Watchdog monitoring setup complete")
            
        except Exception as e:
            self.logger.error(f"Error setting up watchdog: {str(e)}")
            raise

    def _probe_callback(self, pad, info, element_name):
        """Callback for pad probes to monitor data flow"""
        try:
            buffer = info.get_buffer()
            if buffer:
                self.last_activity[element_name] = time.time()
        except Exception as e:
            self.logger.error(f"Error in probe callback for {element_name}: {str(e)}")
        return Gst.PadProbeReturn.OK

    def _watchdog_timeout_callback(self, element_name):
        """Handle watchdog timeouts"""
        if self.handling_failure:
            return True

        try:
            last_time = self.last_activity.get(element_name)
            if last_time and (time.time() - last_time) * 1000 < self.timeouts[element_name]:
                return True

            self.handling_failure = True
            self.logger.error(f"Watchdog timeout for element: {element_name}")
            self._generate_debug_info(element_name)
            
            if not self._handle_failure():
                self.logger.critical("Maximum restart attempts reached, exiting")
                self._force_shutdown()
                return False

            return True
            
        except Exception as e:
            self.logger.error(f"Error in watchdog timeout callback: {str(e)}")
            self._force_shutdown()
            return False

    def _generate_debug_info(self, element_name):
        """Generate debug information for pipeline failure"""
        try:
            # Log element states
            element = self.pipeline.get_by_name(element_name)
            if element:
                state = element.get_state(0)[1]
                self.logger.info(f"Element {element_name} state: {state.value_nick}")
                
                if hasattr(element.props, 'stats'):
                    stats = element.get_property('stats')
                    self.logger.info(f"Element {element_name} stats: {stats.to_string()}")

            # Generate DOT file
            timestamp = int(time.time())
            dot_filename = f"udp_output_failure_{element_name}_{timestamp}"
            Gst.debug_bin_to_dot_file(
                self.pipeline,
                Gst.DebugGraphDetails.ALL,
                dot_filename
            )
            self.logger.info(f"Generated DOT file: {dot_filename}")
            
        except Exception as e:
            self.logger.error(f"Error generating debug info: {str(e)}")

    def _handle_failure(self):
        """Handle pipeline failure and attempt restart"""
        self.restart_attempts += 1
        
        self.logger.warning(
            f"Attempting pipeline restart ({self.restart_attempts}/{self.MAX_RESTART_ATTEMPTS})"
        )

        try:
            self.logger.info(f"Waiting {self.RESTART_DELAY} seconds before restart attempt")
            time.sleep(self.RESTART_DELAY)
            
            # Attempt pipeline recreation
            if not self.handler._recreate_pipeline():
                if self.restart_attempts >= self.MAX_RESTART_ATTEMPTS:
                    self.logger.critical("Maximum restart attempts reached, exiting")
                    self._force_shutdown()
                    return False
                else:
                    # Try again
                    return self._handle_failure()
                
            # Reset watchdog timers
            self.reset_watchdogs()
            self.handling_failure = False
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to restart pipeline: {str(e)}")
            if self.restart_attempts >= self.MAX_RESTART_ATTEMPTS:
                return False
            else:
                # Try again
                return self._handle_failure()

    def reset_watchdogs(self):
        """Reset all watchdog timers"""
        try:
            self.last_activity.clear()
            self.handling_failure = False
            self.logger.debug("Reset all watchdog timers")
        except Exception as e:
            self.logger.error(f"Error resetting watchdogs: {str(e)}")

    def _force_shutdown(self):
        """Force pipeline shutdown and cleanup"""
        self.logger.info("Forcing pipeline shutdown")
        try:
            # Remove watchdog timeouts
            for timer_id in self.watchdog_timers.values():
                GLib.source_remove(timer_id)
            
            # Tell handler to cleanup
            self.handler.cleanup()
            
            # Final exit
            GLib.idle_add(lambda: sys.exit(1))
        except Exception as e:
            self.logger.error(f"Error during force shutdown: {str(e)}")
            sys.exit(1)

    def cleanup(self):
        """Clean up watchdog resources"""
        self.logger.info("Cleaning up watchdog system")
        try:
            # Remove timeout sources
            for timer_id in self.watchdog_timers.values():
                GLib.source_remove(timer_id)
                
            # Clear all internal state
            self.watchdog_timers.clear()
            self.last_activity.clear()
            self.handling_failure = False
            
            self.logger.debug("Watchdog resources cleaned up")
        except Exception as e:
            self.logger.error(f"Error during watchdog cleanup: {str(e)}")

class UDPOutputHandler:
    """Handles UDP output from either transcoded or direct input stream"""
    def __init__(self, channel_name, output_index=0):
        """Initialize the UDP output handler"""
        self.logger = logging.getLogger(__name__)
        self.channel_name = channel_name
        self.output_index = output_index
        
        # Set up DOT file directory
        self.dot_dir = "/root/caricoder/dot"
        Path(self.dot_dir).mkdir(parents=True, exist_ok=True)
        os.environ['GST_DEBUG_DUMP_DOT_DIR'] = self.dot_dir

        self.last_bytes_served = 0
        self.last_time = time.time()
        
        # Initialize configuration
        self.config = Configuration()
        self.channel_settings = self.config.get_channel_settings(channel_name)
        
        # Check if we're in full passthrough mode
        self.is_passthrough = check_full_passthrough(self.config, channel_name)
        
        # Get UDP output settings
        self.outputs = self.channel_settings.get('outputs', [])
        if not self.outputs:
            raise ValueError(f"No outputs defined for channel: {channel_name}")
        
        if output_index < 0 or output_index >= len(self.outputs):
            raise ValueError(f"Invalid output index {output_index}")
        
        self.selected_output = self.outputs[output_index]
        if self.selected_output.get('type') != 'udpsink':
            raise ValueError(f"Output {output_index} is not UDP type")
        
        # Initialize GStreamer
        Gst.init(None)
        
        # Initialize pipeline elements
        self.pipeline = None
        self.elements = {}
        self.watchdog = None
        
        # Initialize Redis for stats
        try:
            self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            self.redis_client.ping()
            self.logger.info("Successfully connected to Redis")
            self.stats_collector = StatsCollector(channel_name, self.redis_client)
        except redis.ConnectionError:
            self.logger.error("Failed to connect to Redis")
            self.redis_client = None
            self.stats_collector = None
        
        self.stats_timer = None

    def _recreate_pipeline(self):
        """Recreate the pipeline with complete reinitialization"""
        self.logger.info("Recreating pipeline after failure with full reset")
        
        try:
            # 1. Full cleanup of existing pipeline
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline.get_state(Gst.CLOCK_TIME_NONE)  # Wait for completion
                self.pipeline = None
            
            # 2. Clear all elements and their references
            for element in self.elements.values():
                element.set_state(Gst.State.NULL)
            self.elements.clear()
            
            # 3. Reset GStreamer system
            #Gst.deinit()
            Gst.init(None)
            
            # 4. Reset internal state variables
            self.last_bytes_served = 0
            self.last_time = time.time()
            
            
            # 5. Short delay to ensure complete cleanup
            time.sleep(1)
            
            # 6. Recreate pipeline fresh
            self.create_pipeline()
            
            # 7. Set straight to PLAYING state
            # Initialize watchdog
            self.watchdog = UDPOutputWatchdog(self.pipeline, self.logger, self)
            self.watchdog.setup_watchdog()
            
            # Generate initial DOT file
            self.generate_dot_file("initial")
            
            # Set up message handling
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_pipeline_message)
            
            # Start the pipeline
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Unable to set the pipeline to the playing state")
            
            # Generate DOT file after pipeline is playing
            self.generate_dot_file("playing")
            
            # Wait for pipeline to completely start
            state_change_ret = self.pipeline.get_state(5 * Gst.SECOND)
            if state_change_ret[0] != Gst.StateChangeReturn.SUCCESS:
                raise RuntimeError(f"Failed to reach PLAYING state: {state_change_ret[1].value_name}")
                
            self.logger.info("Successfully recreated pipeline")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to recreate pipeline: {str(e)}")
            # Generate DOT file on error
            if self.pipeline:
                self.generate_dot_file(f"recreation_failure_{int(time.time())}")
            return False

    def create_pipeline(self):
        """Create the GStreamer pipeline"""
        self.logger.info("Creating UDP output pipeline")
        self.pipeline = Gst.Pipeline.new("udp_output_pipeline")
        
        # Determine input path based on passthrough mode
        socket_dir = "/tmp/caricoder"
        input_path = (f"{socket_dir}/{self.channel_name}_muxed_shm" if self.is_passthrough 
                     else f"{socket_dir}/{self.channel_name}_transcoded_shm")
        
        self.logger.info(f"Pipeline Mode: {'Passthrough' if self.is_passthrough else 'Transcoded'}")
        self.logger.info(f"Using shared memory path: {input_path}")
        
        try:
            # Create elements
            self.elements['shmsrc'] = Gst.ElementFactory.make("shmsrc", "shmsrc")
            if not self.elements['shmsrc']:
                raise RuntimeError("Failed to create shmsrc element")
            
            # Configure source
            self.elements['shmsrc'].set_property('socket-path', input_path)
            self.elements['shmsrc'].set_property('is-live', True)
            self.elements['shmsrc'].set_property('do-timestamp', True)
            
            
            # Create queue
            self.elements['queue1'] = Gst.ElementFactory.make("queue", "queue1")
            if not self.elements['queue1']:
                raise RuntimeError("Failed to create queue element")
            
            # Configure queue with larger buffer
            self.elements['queue1'].set_property("leaky", 1)
            self.elements['queue1'].set_property("max-size-buffers", 0)
            self.elements['queue1'].set_property("max-size-time", 3000000000)
            self.elements['queue1'].set_property("max-size-bytes", 0)
            
            # Create UDP sink
            self.elements['udpsink'] = Gst.ElementFactory.make("udpsink", "udpsink")
            if not self.elements['udpsink']:
                raise RuntimeError("Failed to create udpsink element")
            
            # Configure UDP sink
            host = self.selected_output.get('host', 'localhost')
            port = self.selected_output.get('port', 5000)
            
            self.elements['udpsink'].set_property('host', host)
            self.elements['udpsink'].set_property('port', port)
            self.elements['udpsink'].set_property('sync', True)
            self.elements['udpsink'].set_property('async', True)
            self.elements['udpsink'].set_property('buffer-size', 2097152)  # 2MB buffer
            
            # Add elements to pipeline
            for element in self.elements.values():
                self.pipeline.add(element)
            
            # Link elements
            if not self.elements['shmsrc'].link(self.elements['queue1']):
                raise RuntimeError("Failed to link shmsrc to queue1")
                
            if not self.elements['queue1'].link(self.elements['udpsink']):
                raise RuntimeError("Failed to link queue1 to udpsink")
            
            self.logger.info("Pipeline created successfully")
            
        except Exception as e:
            self.logger.error(f"Error creating pipeline: {str(e)}")
            raise

    def collect_stats(self):
        """Collect and store UDP output statistics"""
        if self.elements.get('udpsink'):
            try:
                current_time = time.time()
                current_bytes = self.elements['udpsink'].get_property('bytes-served')
                
                # Get UDP sink stats
                stats_struct = self.elements['udpsink'].get_property('stats')
                rendered_buffers = stats_struct.get_value('rendered')
                dropped_buffers = stats_struct.get_value('dropped')
                
                # Calculate bitrate
                bytes_per_second = (current_bytes - self.last_bytes_served) / (current_time - self.last_time)
                bitrate_mbps = (bytes_per_second * 8) / (1024 * 1024)
                
                stats = {
                    'bytes_served': current_bytes,
                    'rendered_buffers': rendered_buffers,
                    'dropped_buffers': dropped_buffers,
                    'bitrate_mbps': bitrate_mbps
                }
                
                self.logger.debug(f"UDP Output Statistics: {json.dumps(stats, indent=2)}")
                
                if self.stats_collector:
                    self.stats_collector.add_stats(f"udp_output_{self.output_index}", stats)
                
                # Update values for next calculation
                self.last_bytes_served = current_bytes
                self.last_time = current_time
                
            except Exception as e:
                self.logger.error(f"Error collecting UDP stats: {str(e)}")
                
        return True

    def generate_dot_file(self, filename):
        """Generate a DOT file of the current pipeline state"""
        try:
            full_filename = f"{self.channel_name}_udp_output_{filename}"
            
            Gst.debug_bin_to_dot_file(
                self.pipeline,
                Gst.DebugGraphDetails.ALL,
                full_filename
            )
            
            dot_path = os.path.join(self.dot_dir, f"{full_filename}.dot")
            png_path = os.path.join(self.dot_dir, f"{full_filename}.png")
            
            if not os.path.exists(dot_path):
                self.logger.error(f"DOT file was not created at {dot_path}")
                return
                
            try:
                subprocess.run(
                    ['dot', '-Tpng', dot_path, '-o', png_path],
                    check=True,
                    capture_output=True,
                    text=True
                )
                self.logger.info(f"Generated pipeline visualization: {png_path}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to convert DOT to PNG: {e.stderr}")
            except FileNotFoundError:
                self.logger.error("graphviz 'dot' command not found")
                
        except Exception as e:
            self.logger.error(f"Error generating pipeline visualization: {str(e)}")

    def on_pipeline_message(self, bus, message):
        """Handle pipeline messages"""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.logger.error(f"Pipeline error: {err.message}")
            self.logger.debug(f"Debug info: {debug}")
            
            # Generate DOT file on error
            self.generate_dot_file(f"error_{int(time.time())}")
            
            # Let watchdog handle the error
            if self.watchdog and not self.watchdog.handling_failure:
                self.watchdog.handling_failure = True
                self.watchdog._handle_failure()
                    
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            self.logger.warning(f"Pipeline warning: {warn.message}")
            self.logger.debug(f"Debug info: {debug}")
            
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                self.logger.info(
                    f"Pipeline state changed from {old_state.value_nick} "
                    f"to {new_state.value_nick} "
                    f"(pending: {pending_state.value_nick})"
                )
        elif t == Gst.MessageType.EOS:
            self.logger.warning("End of stream reached")

    def run(self):
        """Main run loop"""
        self.logger.info("Starting UDP output handler")
        
        try:
            # Create initial pipeline
            self.create_pipeline()
            
            # Initialize watchdog
            self.watchdog = UDPOutputWatchdog(self.pipeline, self.logger, self)
            self.watchdog.setup_watchdog()
            
            # Generate initial DOT file
            self.generate_dot_file("initial")
            
            # Set up message handling
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_pipeline_message)
            
            # Start the pipeline
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Unable to set the pipeline to the playing state")
            
            # Generate DOT file after pipeline is playing
            self.generate_dot_file("playing")
            
            # Start statistics collection
            self.stats_timer = GLib.timeout_add(5000, self.collect_stats)
            
            # Run the main loop
            loop = GLib.MainLoop()
            try:
                loop.run()
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
            finally:
                self.cleanup()
                
        except Exception as e:
            self.logger.error(f"Error in UDP output handler: {str(e)}")
            self.cleanup()
            raise

    def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Starting cleanup")
        
        try:
            if self.watchdog:
                self.watchdog.cleanup()
                self.watchdog = None
                self.logger.debug("Watchdog cleaned up")
            
            if self.stats_timer:
                GLib.source_remove(self.stats_timer)
                self.logger.debug("Stopped statistics collection")
            
            if self.pipeline:
                self.logger.info("Stopping pipeline")
                # Set to NULL state and wait for completion
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
                self.pipeline = None
                self.logger.info("Pipeline stopped")
            
            # Clear elements dictionary
            self.elements.clear()
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {str(e)}")
        finally:
            self.logger.info("Cleanup completed")

#main indent
    def _wait_for_shared_memory(self):
        """Wait for shared memory file to be available"""
        # Determine input path based on passthrough mode
        socket_dir = "/tmp/caricoder"
        input_path = (f"{socket_dir}/{self.channel_name}_muxed_shm" if self.is_passthrough 
                     else f"{socket_dir}/{self.channel_name}_transcoded_shm")
        
        max_retries = 30  # 2.5 minutes maximum wait time
        retry_count = 0
        
        self.logger.info(f"Waiting for shared memory file: {input_path}")
        
        while retry_count < max_retries:
            if os.path.exists(input_path):
                self.logger.info(f"Shared memory file is ready: {input_path}")
                return True
                
            retry_count += 1
            self.logger.debug(f"Shared memory file not ready, retry {retry_count}/{max_retries}")
            time.sleep(5)
        
        self.logger.error(f"Shared memory file not available after {max_retries * 5} seconds")
        return False

#main indent
def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="UDP Output Handler")
    parser.add_argument("channel", help="Channel name from the configuration")
    parser.add_argument("--output-index", type=int, default=0,
                       help="Index of the output to use (default: 0)")
    parser.add_argument("--log-dir", default="logs",
                       help="Directory to store log files")
    parser.add_argument("--log-level",
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       default='INFO',
                       help="Set the logging level")
    
    args = parser.parse_args()
    
    # Initialize logging
    logger = setup_logging(args.channel, args.output_index, args.log_dir, args.log_level)
    logger.info(f"Starting UDP Output Handler for channel: {args.channel}")
    logger.info(f"Using output index: {args.output_index}")
    
    try:
        # Create handler
        handler = UDPOutputHandler(args.channel, args.output_index)
        
        # Wait for shared memory
        if not handler._wait_for_shared_memory():
            logger.error("Shared memory not available after timeout, exiting")
            sys.exit(1)
        
        # Start the handler
        handler.run()
    except KeyboardInterrupt:
        logger.info("Exiting due to keyboard interrupt")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()