#!/usr/bin/env python3

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
from config import Configuration
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import sys
import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

def setup_logging(channel_name, log_dir='logs', log_level='INFO'):
    """Configure logging with both console and file outputs"""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'hls_output_{channel_name}_{timestamp}.log')
    
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

#main indent
class HLSOutputHandler:
    def __init__(self, channel_name, output_index=0, mode='output'):
        self.logger = logging.getLogger(__name__)
        self.channel_name = channel_name
        self.output_index = output_index
        self.mode = mode
        
        # Set up directories
        self.socket_dir = "/tmp/caricoder"
        self.dot_dir = "/root/caricoder/dot"
        Path(self.dot_dir).mkdir(parents=True, exist_ok=True)
        os.environ['GST_DEBUG_DUMP_DOT_DIR'] = self.dot_dir
        
        # Initialize GStreamer
        Gst.init(None)
        
        # Initialize configuration
        self.config = Configuration()
        self.channel_settings = self.config.get_channel_settings(channel_name)
        
        # Get HLS output settings
        self.outputs = self.channel_settings.get('outputs', [])
        if not self.outputs:
            raise ValueError(f"No outputs defined for channel: {channel_name}")
            
        if output_index < 0 or output_index >= len(self.outputs):
            raise ValueError(f"Invalid output index {output_index}")
        
        # Initialize pipeline elements
        self.pipeline = None
        self.elements = {}
        
        # Set default HLS options
        self.hls_options = {
            'playlist-length': 5,
            'target-duration': 10,
            'send-keyframe-requests': False,
            'max-files': 6,
            'playlist-location': None,
            'location': None
        }

#main indent
    def _get_parser_types(self):
        """Determine which parsers to use based on input source and transcoding config"""
        video_parser = None
        audio_parser = None
        max_retries = 10
        retry_count = 0

        while retry_count < max_retries:
            try:
                video_info_path = f"{self.socket_dir}/{self.channel_name}_video_shm_info"
                audio_info_path = f"{self.socket_dir}/{self.channel_name}_audio_shm_info"
                
                if not (os.path.exists(video_info_path) and os.path.exists(audio_info_path)):
                    retry_count += 1
                    self.logger.info(f"Waiting for codec info files... Attempt {retry_count}/{max_retries}")
                    time.sleep(5)
                    continue

                if self.mode == 'input':
                    with open(video_info_path, 'r') as f:
                        video_info = json.loads(f.read())
                    with open(audio_info_path, 'r') as f:
                        audio_info = json.loads(f.read())
                    
                    video_codec = video_info.get('codec')
                    audio_codec = audio_info.get('codec')
                    
                    video_parser = {
                        'h264': 'h264parse',
                        'hevc': 'h265parse',
                        'mpeg2video': 'mpegvideoparse'
                    }.get(video_codec)
                    
                    audio_parser = {
                        'aac': 'aacparse',
                        'mp2': 'mpegaudioparse',
                        'mp3': 'mpegaudioparse'
                    }.get(audio_codec)
                    
                else:  # mode == 'output'
                    transcoding = self.channel_settings.get('transcoding', {})
                    
                    video_settings = transcoding.get('video', {})
                    if isinstance(video_settings.get('streams'), list):
                        video_codec = video_settings['streams'][0].get('codec', '')
                    else:
                        video_codec = video_settings.get('codec', '')
                    
                    if video_codec == 'passthrough':
                        with open(video_info_path, 'r') as f:
                            video_info = json.loads(f.read())
                        video_codec = video_info.get('codec')
                    
                    video_parser = {
                        'x264enc': 'h264parse',
                        'x265enc': 'h265parse',
                        'h264': 'h264parse',
                        'hevc': 'h265parse',
                        'mpeg2video': 'mpegvideoparse'
                    }.get(video_codec)
                    
                    audio_settings = transcoding.get('audio', {})
                    audio_codec = audio_settings.get('codec', '')
                    
                    if audio_codec == 'passthrough':
                        with open(audio_info_path, 'r') as f:
                            audio_info = json.loads(f.read())
                        audio_codec = audio_info.get('codec')
                    
                    audio_parser = {
                        'avenc_aac': 'aacparse',
                        'avenc_mp2': 'mpegaudioparse',
                        'aac': 'aacparse',
                        'mp2': 'mpegaudioparse',
                        'mp3': 'mpegaudioparse'
                    }.get(audio_codec)
                
                if not video_parser or not audio_parser:
                    raise ValueError(f"Unsupported codec combination: video={video_codec}, audio={audio_codec}")
                
                self.logger.info(f"Selected parsers - Video: {video_parser}, Audio: {audio_parser}")
                return video_parser, audio_parser
                
            except (FileNotFoundError, json.JSONDecodeError) as e:
                if retry_count == max_retries - 1:
                    self.logger.error(f"Error determining parser types after {max_retries} retries: {str(e)}")
                    raise
                retry_count += 1
                time.sleep(5)


#main indent
    def create_pipeline(self):
        """Create the GStreamer pipeline for HLS output"""
        self.logger.info("Creating HLS output pipeline")
        self.pipeline = Gst.Pipeline.new("hls_output_pipeline")
        
        # Get parser types based on mode and configuration
        video_parser_type, audio_parser_type = self._get_parser_types()
        
        # Set up HLS output directory
        hls_dir = f"/var/www/html/content/{self.channel_name}"
        try:
            os.makedirs(hls_dir, mode=0o777, exist_ok=True)
        except OSError as e:
            self.logger.error(f"Failed to create HLS directory {hls_dir}: {str(e)}")
            raise
        
        # Verify directory exists and is writable
        if not os.path.exists(hls_dir):
            raise RuntimeError(f"HLS directory {hls_dir} does not exist")
        if not os.access(hls_dir, os.W_OK):
            raise RuntimeError(f"HLS directory {hls_dir} is not writable") 
       
        # Set file locations
        self.hls_options['playlist-location'] = f"{hls_dir}/playlist.m3u8"
        self.hls_options['location'] = f"{hls_dir}/segment%05d.ts"
        
        # Determine input path based on mode
        input_path = (f"{self.socket_dir}/{self.channel_name}_muxed_shm" if self.mode == 'input' 
                     else f"{self.socket_dir}/{self.channel_name}_transcoded_shm")
        
        try:
            # Create elements
            self.elements.update({
                'shmsrc': Gst.ElementFactory.make("shmsrc", "shmsrc"),
                'queue1': Gst.ElementFactory.make("queue", "queue1"),
                'tsparse': Gst.ElementFactory.make("tsparse", "tsparse"),
                'queue2': Gst.ElementFactory.make("queue", "queue2"),
                'tsdemux': Gst.ElementFactory.make("tsdemux", "tsdemux"),
                'queue_audio': Gst.ElementFactory.make("queue", "queue_audio"),
                'queue_video': Gst.ElementFactory.make("queue", "queue_video"),
                'videoparse': Gst.ElementFactory.make(video_parser_type, "videoparse"),
                'audioparse': Gst.ElementFactory.make(audio_parser_type, "audioparse"),
                'video_watchdog': Gst.ElementFactory.make("watchdog", "video_watchdog"),  # Added
                'audio_watchdog': Gst.ElementFactory.make("watchdog", "audio_watchdog"),  # Added
                'queue_audio_out': Gst.ElementFactory.make("queue", "queue_audio_out"),
                'queue_video_out': Gst.ElementFactory.make("queue", "queue_video_out"),
                'mux': Gst.ElementFactory.make("hlssink2", "mux")
            })

            # Configure watchdogs
            self.elements['video_watchdog'].set_property('timeout', 15000)  # 5 second timeout
            self.elements['audio_watchdog'].set_property('timeout', 15000)  # 5 second timeout
            
            # Configure source
            self.elements['shmsrc'].set_property('socket-path', input_path)
            self.elements['shmsrc'].set_property('is-live', True)
            
            # Configure queues
            queue_props = {
                "leaky": 1,
                "max-size-time": 500000000000,
                "max-size-buffers": 1000000
            }
            
            for queue in ['queue1', 'queue2', 'queue_audio', 'queue_video', 'queue_audio_out', 'queue_video_out']:
                for prop, value in queue_props.items():
                    self.elements[queue].set_property(prop, value)
            
            # Configure tsparse
            self.elements['tsparse'].set_property('set-timestamps', True)
            self.elements['tsparse'].set_property('smoothing-latency', 1000)
            
            #main indent
            # Configure HLS sink
            for key, value in self.hls_options.items():
                if value is not None:
                    self.elements['mux'].set_property(key, value)
                    self.logger.info(f"Set HLS property: {key}={value}")

            # Configure the splitmuxsink child element
            splitmuxsink = self.elements['mux'].get_by_name('splitmuxsink0')
            if splitmuxsink:
                splitmuxsink.set_property('max-size-time', 10 * Gst.SECOND)
                self.logger.info("Set splitmuxsink max-size-time to 10 seconds")
            else:
                self.logger.error("Could not find splitmuxsink element")

            
            # Add all elements to pipeline
            for element in self.elements.values():
                if not element:
                    raise RuntimeError(f"Failed to create element {element}")
                self.pipeline.add(element)
            
            # Link static elements
            if not self.elements['shmsrc'].link(self.elements['queue1']):
                raise RuntimeError("Failed to link shmsrc to queue1")
            
            if not self.elements['queue1'].link(self.elements['tsparse']):
                raise RuntimeError("Failed to link queue1 to tsparse")
            
            if not self.elements['tsparse'].link(self.elements['queue2']):  # Changed this line
                raise RuntimeError("Failed to link tsparse to queue2")      # Changed this line

            if not self.elements['queue2'].link(self.elements['tsdemux']):  # Added this line
                raise RuntimeError("Failed to link queue2 to tsdemux") 
            
            # Setup pad-added handler for tsdemux
            def on_pad_added(element, pad):
                pad_name = pad.get_name()
                if pad_name.startswith('video'):
                    # Link video chain
                    sink_pad = self.elements['queue_video'].get_static_pad('sink')
                    if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                        self.logger.info("Linked video pad successfully")
                        # Now link the rest of the video chain
                        # Now link the rest of the video chain with watchdog
                        if not self.elements['queue_video'].link(self.elements['video_watchdog']):
                            self.logger.error("Failed to link queue_video to video_watchdog")
                            return
                        if not self.elements['video_watchdog'].link(self.elements['videoparse']):
                            self.logger.error("Failed to link video_watchdog to videoparse")
                            return
                        if not self.elements['videoparse'].link(self.elements['queue_video_out']):
                            self.logger.error("Failed to link videoparse to queue_video_out")
                            return
                        if not self.elements['queue_video_out'].link_pads('src', self.elements['mux'], 'video'):
                            self.logger.error("Failed to link queue_video_out to mux")
                            return
                        self.logger.info("Successfully linked complete video chain")
                        #self.elements['queue_video'].link(self.elements['videoparse'])
                        #self.elements['videoparse'].link(self.elements['queue_video_out'])
                        #self.elements['queue_video_out'].link_pads('src', self.elements['mux'], 'video')
                    else:
                        self.logger.error("Failed to link video pad")
                        
                elif pad_name.startswith('audio'):
                    # Link audio chain
                    sink_pad = self.elements['queue_audio'].get_static_pad('sink')
                    if pad.link(sink_pad) == Gst.PadLinkReturn.OK:
                        self.logger.info("Linked audio pad successfully")
                        if not self.elements['queue_audio'].link(self.elements['audio_watchdog']):
                            self.logger.error("Failed to link queue_audio to audio_watchdog")
                            return
                        if not self.elements['audio_watchdog'].link(self.elements['audioparse']):
                            self.logger.error("Failed to link audio_watchdog to audioparse")
                            return
                        if not self.elements['audioparse'].link(self.elements['queue_audio_out']):
                            self.logger.error("Failed to link audioparse to queue_audio_out")
                            return
                        if not self.elements['queue_audio_out'].link_pads('src', self.elements['mux'], 'audio'):
                            self.logger.error("Failed to link queue_audio_out to mux")
                            return
                        self.logger.info("Successfully linked complete audio chain")
                        # Now link the rest of the audio chain
                        #self.elements['queue_audio'].link(self.elements['audioparse'])
                        #self.elements['audioparse'].link(self.elements['queue_audio_out'])
                        #self.elements['queue_audio_out'].link_pads('src', self.elements['mux'], 'audio')
                    else:
                        self.logger.error("Failed to link audio pad")
            
            self.elements['tsdemux'].connect('pad-added', on_pad_added)
            
            self.logger.info("Pipeline created successfully")
            
        except Exception as e:
            self.logger.error(f"Error creating pipeline: {str(e)}")
            raise

#main indent
    def _wait_for_shared_memory(self):
        """Wait for all required shared memory files to be available"""
        # Determine input path based on mode
        input_path = (f"{self.socket_dir}/{self.channel_name}_muxed_shm" if self.mode == 'input' 
                     else f"{self.socket_dir}/{self.channel_name}_transcoded_shm")
        
        # Define all required files
        required_files = [
            input_path,
            f"{self.socket_dir}/{self.channel_name}_video_shm_info",
            f"{self.socket_dir}/{self.channel_name}_audio_shm_info"
        ]
        
        max_retries = 30  # 2.5 minutes maximum wait time
        retry_count = 0
        
        self.logger.info(f"Waiting for shared memory files: {', '.join(required_files)}")
        
        while retry_count < max_retries:
            # Check if all files exist
            missing_files = [f for f in required_files if not os.path.exists(f)]
            
            if not missing_files:
                self.logger.info("All shared memory files are ready")
                return True
                
            retry_count += 1
            self.logger.debug(f"Missing files: {', '.join(missing_files)}")
            self.logger.debug(f"Retry {retry_count}/{max_retries}")
            time.sleep(5)
        
        self.logger.error(f"Required shared memory files not available after {max_retries * 5} seconds")
        return False

#main indent
    def _handle_watchdog_timeout(self):
        """Handle pipeline failure and attempt restart"""
        self.logger.info("Handling watchdog timeout")
        try:
            # Stop current pipeline
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
                self.pipeline = None
            
            # Clear elements
            for element in self.elements.values():
                element.set_state(Gst.State.NULL)
            self.elements.clear()

            # Short delay to ensure complete cleanup
            time.sleep(1)
            
            # Wait for shared memory
            if not self._wait_for_shared_memory():
                raise RuntimeError("Shared memory not available - cannot restart pipeline")
                    
            # Re-create pipeline
            self.create_pipeline()
            
            # Set up message handling
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_pipeline_message)
            
            # Generate DOT file before starting
            self.generate_dot_file("restart_attempt")
            
            # Start the pipeline
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Unable to set the pipeline to the playing state")
                
            self.logger.info("Successfully restarted pipeline after watchdog timeout")
            
        except Exception as e:
            self.logger.error(f"Failed to restart pipeline: {str(e)}")
            raise

#main indent
    def on_pipeline_message(self, bus, message):
        """Handle pipeline messages"""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            error_msg = err.message
            element_name = message.src.get_name()
            
            # Handle watchdog timeout
            if 'Watchdog triggered' in error_msg:
                self.logger.error(f"Watchdog timeout detected in {element_name}")
                try:
                    self._handle_watchdog_timeout()
                except Exception as e:
                    self.logger.error(f"Failed to recover from watchdog timeout: {str(e)}")
            else:
                self.logger.error(f"Pipeline error from element {element_name}: {error_msg}")
                self.logger.error(f"Debug info: {debug}")
                self.generate_dot_file(f"error_{int(time.time())}")
                
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            element_name = message.src.get_name()
            self.logger.warning(f"Pipeline warning from element {element_name}: {warn.message}")
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
            self.logger.warning("End of stream received - attempting pipeline restart")
            try:
                self._handle_watchdog_timeout()  # Reuse the same restart mechanism
            except Exception as e:
                self.logger.error(f"Failed to recover from EOS: {str(e)}")

    def generate_dot_file(self, filename):
        """Generate a DOT file of the current pipeline state"""
        try:
            full_filename = f"{self.channel_name}_hls_output_{filename}"
            
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

    def run(self):
        """Main run loop"""
        self.logger.info("Starting HLS output handler")
        
        try:
            # Create initial pipeline
            self.create_pipeline()
            
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
            
            # Run the main loop
            loop = GLib.MainLoop()
            try:
                loop.run()
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
            finally:
                self.cleanup()
                
        except Exception as e:
            self.logger.error(f"Error in HLS output handler: {str(e)}")
            self.cleanup()
            raise

    def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Starting cleanup")
        
        if self.pipeline:
            self.logger.info("Stopping pipeline")
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_state(Gst.CLOCK_TIME_NONE)
            self.logger.info("Pipeline stopped")
        
        # Clear elements dictionary
        self.elements.clear()
        
        self.logger.info("Cleanup completed")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="HLS Output Handler")
    parser.add_argument("channel", help="Channel name from the configuration")
    parser.add_argument("--output-index", type=int, default=0,
                       help="Index of the output to use (default: 0)")
    parser.add_argument("--mode", choices=['input', 'output'],
                       default='output',
                       help="Use input or output shared memory (default: output)")
    parser.add_argument("--log-dir", default="logs",
                       help="Directory to store log files")
    parser.add_argument("--log-level",
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                       default='INFO',
                       help="Set the logging level")
    
    args = parser.parse_args()
    
    # Initialize logging
    logger = setup_logging(args.channel, args.log_dir, args.log_level)
    logger.info(f"Starting HLS Output Handler for channel: {args.channel}")
    logger.info(f"Using output index: {args.output_index}")
    logger.info(f"Mode: {args.mode}")
    
    try:
        # Create handler
        handler = HLSOutputHandler(args.channel, args.output_index, args.mode)
        
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