from flask import Flask, jsonify, request
from flask_cors import CORS
import redis
import json
import time
import traceback
import yaml
import os
import subprocess

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

# Path to the YAML configuration file
CONFIG_PATH = "/root/caricoder/config.yaml"

# Function to read YAML config
def read_yaml_config():
    try:
        if not os.path.exists(CONFIG_PATH):
            raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")
        
        with open(CONFIG_PATH, 'r') as file:
            config = yaml.safe_load(file)
        
        if not config or 'channels' not in config:
            raise ValueError("Invalid configuration format")
        
        return config
    except Exception as e:
        app.logger.error(f"Error reading YAML config: {str(e)}")
        app.logger.error(traceback.format_exc())
        return None

# Channel information endpoint
@app.route('/api/channels')
def get_channels():
    try:
        config = read_yaml_config()
        if config is None:
            return jsonify({"error": "Error reading configuration", 
                          "details": "Check server logs for more information"}), 500

        channels_data = []
        for channel_name, channel_info in config['channels'].items():
            channel_data = {
                "name": channel_name,
                "inputs": channel_info.get('inputs', []),
                "outputs": channel_info.get('outputs', []),
                "transcoding": channel_info.get('transcoding', {}),
                "mux": channel_info.get('mux', {})
            }
            channels_data.append(channel_data)

        return jsonify({"channels": channels_data})
    except Exception as e:
        app.logger.error(f"Error in get_channels: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred", "details": str(e)}), 500

# Stats endpoints for all types (SRT, encoder, UDP)
@app.route('/stats/live/<channel_name>/<stat_type>')
def get_live_stats(channel_name, stat_type):
    try:
        key = f"channel:{channel_name}:{stat_type}:live"
        stats = redis_client.zrange(key, 0, -1, withscores=True)
        return jsonify([{"timestamp": int(timestamp), "stats": json.loads(stats_json)} 
                       for stats_json, timestamp in stats])
    except Exception as e:
        app.logger.error(f"Error in get_live_stats: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred"}), 500

@app.route('/stats/historic/<channel_name>/<stat_type>')
def get_historic_stats(channel_name, stat_type):
    try:
        key = f"channel:{channel_name}:{stat_type}:historic"
        stats = redis_client.zrange(key, 0, -1, withscores=True)
        return jsonify([{"timestamp": int(timestamp), "stats": json.loads(stats_json)} 
                       for stats_json, timestamp in stats])
    except Exception as e:
        app.logger.error(f"Error in get_historic_stats: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred"}), 500

# System metrics endpoints
@app.route('/metrics/live/<metric_name>')
def get_live_metrics(metric_name):
    try:
        if metric_name == 'network':
            interfaces = redis_client.keys("live:network:*")
            network_data = {}
            for interface_key in interfaces:
                interface = interface_key.split(':')[-1]
                data = redis_client.lrange(interface_key, 0, -1)
                network_data[interface] = [json.loads(item) for item in data]
            return jsonify(network_data)
        else:
            key = f"live:{metric_name}"
            data = redis_client.lrange(key, 0, -1)
            return jsonify([json.loads(item) for item in data])
    except Exception as e:
        app.logger.error(f"Error in get_live_metrics: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred"}), 500

@app.route('/metrics/historic/<metric_name>')
def get_historic_metrics(metric_name):
    try:
        if metric_name == 'network':
            interfaces = redis_client.keys("historic:network:*")
            network_data = {}
            for interface_key in interfaces:
                interface = interface_key.split(':')[-1]
                data = redis_client.lrange(interface_key, 0, -1)
                network_data[interface] = [json.loads(item) for item in data]
            return jsonify(network_data)
        else:
            key = f"historic:{metric_name}"
            data = redis_client.lrange(key, 0, -1)
            return jsonify([json.loads(item) for item in data])
    except Exception as e:
        app.logger.error(f"Error in get_historic_metrics: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred"}), 500

@app.route('/metrics/latest')
def get_latest_metrics():
    try:
        metrics = ['cpu', 'memory', 'hdd', 'gpu', 'channels']
        latest_data = {}
        
        # Get system metrics
        for metric in metrics:
            key = f"live:{metric}"
            data = redis_client.lrange(key, 0, 0)
            if data:
                latest_data[metric] = json.loads(data[0])
        
        # Handle network separately
        interfaces = redis_client.keys("live:network:*")
        network_data = {}
        for interface_key in interfaces:
            interface = interface_key.split(':')[-1]
            data = redis_client.lrange(interface_key, 0, 0)
            if data:
                network_data[interface] = json.loads(data[0])
        latest_data['network'] = network_data

        return jsonify(latest_data)
    except Exception as e:
        app.logger.error(f"Error in get_latest_metrics: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({"error": "An internal error occurred"}), 500

# Helper endpoint to list available stat types
@app.route('/stats/types')
def get_stat_types():
    """Get all valid stat type values that can be used in the API"""
    return jsonify({
        "stat_types": [
            "srt_input",
            "video_encoder_input",
            "video_encoder_output",
            "udp_output"
        ]
    })


#main indent
@app.route('/stream/info/<channel_name>')
def get_stream_info(channel_name):
    """Get comprehensive stream information for a channel supporting all input types."""
    try:
        socket_dir = "/tmp/caricoder"
        video_info_path = f"{socket_dir}/{channel_name}_video_shm_info"
        audio_info_path = f"{socket_dir}/{channel_name}_audio_shm_info"
        
        # Check if files exist
        if not os.path.exists(video_info_path) or not os.path.exists(audio_info_path):
            return jsonify({
                "error": "Stream info not found",
                "details": "Channel may not be running"
            }), 404
            
        # Read codec info files
        with open(video_info_path, 'r') as f:
            video_info = json.load(f)
        with open(audio_info_path, 'r') as f:
            audio_info = json.load(f)
        
        # Read current configuration
        config = read_yaml_config()
        channel_config = config['channels'].get(channel_name, {}) if config else {}
        
        # Basic stream status - always available regardless of input type
        stream_info = {
            "name": channel_name,
            "status": {
                "video": {
                    "codec": video_info.get('codec'),
                    "pid": video_info.get('pid'),
                    "program_number": video_info.get('program_number')
                },
                "audio": {
                    "codec": audio_info.get('codec'),
                    "pid": audio_info.get('pid'),
                    "program_number": audio_info.get('program_number')
                }
            },
            "config": {
                "inputs": channel_config.get('inputs', []),
                "transcoding": channel_config.get('transcoding', {}),
                "outputs": channel_config.get('outputs', [])
            }
        }
        
        # Handle extended information if available (works for any input type)
        if 'extended' in video_info:
            ext_video = video_info['extended']
            ext_audio = audio_info.get('extended', {})
            
            # Add detailed information
            stream_info["details"] = {
                "input": {
                    "type": ext_video['input'].get('type'),
                    "uri": ext_video['input'].get('uri'),
                    "format": ext_video['input'].get('format'),
                    "streams": ext_video['input'].get('nb_streams'),
                    "programs": ext_video['input'].get('nb_programs')
                },
                "program": ext_video['program'],  # Include full program info
                "video": {
                    "codec": {
                        "name": ext_video['stream']['codec'].get('name'),
                        "long_name": ext_video['stream']['codec'].get('long_name'),
                        "profile": ext_video['stream']['codec'].get('profile'),
                        "level": ext_video['stream']['codec'].get('level')
                    },
                    "format": {
                        "width": ext_video['stream']['format'].get('width'),
                        "height": ext_video['stream']['format'].get('height'),
                        "coded_width": ext_video['stream']['format'].get('coded_width'),
                        "coded_height": ext_video['stream']['format'].get('coded_height'),
                        "pix_fmt": ext_video['stream']['format'].get('pix_fmt'),
                        "aspect_ratio": {
                            "sample": ext_video['stream']['format'].get('sample_aspect_ratio'),
                            "display": ext_video['stream']['format'].get('display_aspect_ratio')
                        },
                        "color": {
                            "range": ext_video['stream']['format'].get('color_range'),
                            "chroma_location": ext_video['stream']['format'].get('chroma_location'),
                            "field_order": ext_video['stream']['format'].get('field_order')
                        }
                    },
                    "encoding": ext_video['stream']['encoding'],  # Include all encoding params
                    "timing": ext_video['stream']['timing'],      # Include all timing info
                    "tags": ext_video['stream'].get('tags', {})
                },
                "audio": {
                    "codec": ext_audio.get('stream', {}).get('codec', {}),
                    "format": ext_audio.get('stream', {}).get('format', {}),
                    "timing": ext_audio.get('stream', {}).get('timing', {}),
                    "tags": ext_audio.get('stream', {}).get('tags', {})
                }
            }
            
            # Store raw extended data for full access if needed
            stream_info["raw"] = {
                "video": video_info,
                "audio": audio_info
            }
        
        return jsonify(stream_info)
        
    except Exception as e:
        app.logger.error(f"Error getting stream info: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            "error": "An internal error occurred",
            "details": str(e)
        }), 500


@app.route('/probe', methods=['POST'])
def probe_stream():
    try:
        data = request.get_json()
        url = data.get('url')
        if not url:
            return jsonify({'error': 'Missing URL parameter'}), 400

        # Run ffprobe command to get stream information
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', '-show_programs', '-i' , url]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            return jsonify({'error': f'FFprobe error: {stderr.decode().strip()}'}), 500

        # Parse the FFprobe output
        stream_info = json.loads(stdout.decode())

        # Extract program information
        programs = []
        for program in stream_info.get('programs', []):
            program_data = {
                'program_id': program.get('program_id', 0),
                'program_num': program.get('program_number', 0),
                'nb_streams': program.get('nb_streams', 0),
                'pmt_pid': program.get('pmt_pid', 0),
                'pcr_pid': program.get('pcr_pid', 0),
                'tags': program.get('tags', {}),
                'streams': []
            }

            for stream in program.get('streams', []):
                stream_data = {
                    'index': stream.get('index', 0),
                    'codec_name': stream.get('codec_name', ''),
                    'codec_long_name': stream.get('codec_long_name', ''),
                    'profile': stream.get('profile', ''),
                    'codec_type': stream.get('codec_type', ''),
                    'codec_tag_string': stream.get('codec_tag_string', ''),
                    'codec_tag': stream.get('codec_tag', 0),
                    'width': stream.get('width', 0),
                    'height': stream.get('height', 0),
                    'coded_width': stream.get('coded_width', 0),
                    'coded_height': stream.get('coded_height', 0),
                    'sample_fmt': stream.get('sample_fmt', ''),
                    'sample_rate': stream.get('sample_rate', 0),
                    'channels': stream.get('channels', 0),
                    'channel_layout': stream.get('channel_layout', ''),
                    'bits_per_sample': stream.get('bits_per_sample', 0),
                    'initial_padding': stream.get('initial_padding', 0),
                    'ts_packetsize': stream.get('ts_packetsize', 0),
                    'id': stream.get('id', 0),
                    'r_frame_rate': stream.get('r_frame_rate', ''),
                    'avg_frame_rate': stream.get('avg_frame_rate', ''),
                    'time_base': stream.get('time_base', ''),
                    'start_pts': stream.get('start_pts', 0),
                    'start_time': stream.get('start_time', ''),
                    'bit_rate': stream.get('bit_rate', 0),
                    'disposition': stream.get('disposition', {}),
                    'tags': stream.get('tags', {})
                }
                program_data['streams'].append(stream_data)

            programs.append(program_data)

        return jsonify({'programs': programs})

    except Exception as e:
        app.logger.error(f"Error in probe_stream: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({'error': 'An internal error occurred'}), 500


# Debug endpoints
@app.route('/debug/redis/<key>')
def debug_redis(key):
    try:
        value = redis_client.zrange(key, 0, -1, withscores=True)
        return jsonify({"key": key, "value": value})
    except Exception as e:
        app.logger.error(f"Error in debug_redis: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500

@app.route('/trigger_aggregation/<channel_name>/<stat_type>')
def trigger_aggregation(channel_name, stat_type):
    from stats_collector import StatsCollector
    collector = StatsCollector(channel_name, redis_client)
    timestamp = int(time.time())
    collector._aggregate_historic_stats(stat_type, timestamp)
    return jsonify({"message": "Aggregation triggered", "timestamp": timestamp})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)