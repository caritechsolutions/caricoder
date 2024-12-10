import yaml
import os
from typing import Dict, Any, List

class Configuration:
    def __init__(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_file = os.path.join(current_dir, 'config.yaml')
        self.config = self._read_config()

    def _read_config(self) -> Dict[str, Any]:
        with open(self.config_file, 'r') as file:
            return yaml.safe_load(file)

    def get_channel_settings(self, channel_name: str) -> Dict[str, Any]:
        if channel_name not in self.config['channels']:
            raise ValueError(f"Channel '{channel_name}' not found in configuration")
        return self.config['channels'][channel_name]

    def get_input_settings(self, channel_name: str) -> List[Dict[str, Any]]:
        channel_settings = self.get_channel_settings(channel_name)
        return channel_settings.get('inputs', [])

    def get_output_settings(self, channel_name: str) -> List[Dict[str, Any]]:
        channel_settings = self.get_channel_settings(channel_name)
        return channel_settings.get('outputs', [])

    def get_transcoding_settings(self, channel_name: str) -> Dict[str, Any]:
        channel_settings = self.get_channel_settings(channel_name)
        transcoding = channel_settings.get('transcoding', {})
        
        # Handle new deinterlace and resolution options
        video_settings = transcoding.get('video', {})
        if 'deinterlace' in video_settings:
            video_settings['deinterlace'] = bool(video_settings['deinterlace'])
        if 'resolution' in video_settings:
            resolution = video_settings['resolution']
            if isinstance(resolution, dict):
                video_settings['resolution'] = {
                    'width': int(resolution.get('width', 0)),
                    'height': int(resolution.get('height', 0))
                }
            else:
                video_settings['resolution'] = None
        
        return transcoding

    def get_mux_settings(self, channel_name: str) -> Dict[str, Any]:
        channel_settings = self.get_channel_settings(channel_name)
        return channel_settings.get('mux', {})

    def get_plugin_settings(self, channel_name: str, plugin_type: str) -> Dict[str, Any]:
        channel_settings = self.get_channel_settings(channel_name)
        
        if plugin_type in ['srtsrc', 'udpsrc']:
            inputs = channel_settings.get('inputs', [])
            input_data = next((input for input in inputs if input['type'] == plugin_type), {})
            return {
                **input_data.get('options', {}),
                'uri': input_data.get('uri'),
                'demux': input_data.get('demux', {})
            }
        elif plugin_type in ['x264enc', 'x265enc']:
            transcoding = channel_settings.get('transcoding', {})
            video_settings = transcoding.get('video', {})
            return {
                **video_settings.get('options', {}),
                'deinterlace': video_settings.get('deinterlace', False),
                'resolution': video_settings.get('resolution')
            }
        elif plugin_type in ['avenc_aac', 'avenc_mp2']:
            transcoding = channel_settings.get('transcoding', {})
            return transcoding.get('audio', {}).get('options', {})
        elif plugin_type in ['udpsink', 'rtmpsink', 'tcpserversink', 'ristsink']:
            outputs = channel_settings.get('outputs', [])
            return next((output for output in outputs if output['type'] == plugin_type), {})
        elif plugin_type == 'mpegtsmux':
            return channel_settings.get('mux', {})
        else:
            return {}

    def validate_plugin_settings(self, plugin_type: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        valid_settings = {}

        if plugin_type in ['srtsrc', 'udpsrc']:
            valid_keys = ['uri', 'latency', 'streamid', 'mode', 'passphrase', 'pbkeylen', 'do-timestamp', 'buffer-size']
            for key in valid_keys:
                if key in settings:
                    valid_settings[key] = settings[key]
            if 'demux' in settings:
                valid_settings['demux'] = {
                    'program-number': int(settings['demux'].get('program-number', 0)),
                    'video-pid': int(settings['demux'].get('video-pid', '0x0'), 16),
                    'audio-pid': int(settings['demux'].get('audio-pid', '0x0'), 16)
                }

        elif plugin_type in ['x264enc', 'x265enc']:
            if 'bitrate' in settings:
                valid_settings['bitrate'] = int(settings['bitrate']) * 1000  # Convert to bps
            for key in ['key-int-max', 'bframes']:
                if key in settings:
                    valid_settings[key] = int(settings[key])
            if 'tune' in settings:
                valid_settings['tune'] = settings['tune']
            if 'deinterlace' in settings:
                valid_settings['deinterlace'] = bool(settings['deinterlace'])
            if 'resolution' in settings and isinstance(settings['resolution'], dict):
                valid_settings['resolution'] = {
                    'width': int(settings['resolution'].get('width', 0)),
                    'height': int(settings['resolution'].get('height', 0))
                }

        elif plugin_type in ['avenc_aac', 'avenc_mp2']:
            if 'bitrate' in settings:
                valid_settings['bitrate'] = int(settings['bitrate']) * 1000  # Convert to bps

        elif plugin_type in ['udpsink', 'rtmpsink', 'tcpserversink', 'ristsink']:
            if 'host' in settings:
                valid_settings['host'] = settings['host']
            if 'port' in settings:
                valid_settings['port'] = int(settings['port'])
            if 'location' in settings:  # for rtmpsink
                valid_settings['location'] = settings['location']

        elif plugin_type == 'mpegtsmux':
            if 'bitrate' in settings:
                valid_settings['bitrate'] = int(settings['bitrate']) * 1000  # Convert to bps
            if 'program-number' in settings:
                valid_settings['program-number'] = int(settings['program-number'])
            if 'video-pid' in settings:
                valid_settings['video-pid'] = int(settings['video-pid'], 16)
            if 'audio-pid' in settings:
                valid_settings['audio-pid'] = int(settings['audio-pid'], 16)

        return valid_settings

    def update_channel_settings(self, channel_name: str, settings: Dict[str, Any]):
        if channel_name not in self.config['channels']:
            self.config['channels'][channel_name] = {}
        self.config['channels'][channel_name].update(settings)

    def save_config(self):
        with open(self.config_file, 'w') as file:
            yaml.dump(self.config, file)