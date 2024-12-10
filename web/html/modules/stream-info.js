// stream-info.js
import { channelState } from './state.js';



class StreamInfoManager {
    constructor() {
        this.previousValues = new Map();
        this.updateInterval = null;
        this.info = null;
    }


    async fetchInfo() {
        try {
            const channelName = new URLSearchParams(window.location.search).get('name');
            const server = localStorage.getItem('selectedServer');

            const serverSelect = document.getElementById('server-select');
            const serverAddress = serverSelect?.value;
           

            const response = await fetch(`http://${serverAddress}:5000/stream/info/${channelName}`);
            
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            
            const data = await response.json();
            this.processStreamInfo(data);
            return this.info;
        } catch (error) {
            console.error('Error fetching stream info:', error);
            return null;
        }
    }


    async fetchStatus() {
        try {
            const response = await fetch(`http://${channelState.server}:8001/status`);
            const data = await response.json();
        
            // Find our channel's status
            const channelStatus = data.channels?.[channelState.name];
            if (channelStatus?.processes) {
               // console.log(channelStatus.processes.input.uptime);
                return {
                    uptime: channelStatus.processes.input.uptime
                    
                };
            }
            return null;
        } catch (error) {
            console.error('Error fetching status:', error);
            return null;
        }
    }


    processStreamInfo(data) {
        const { details, status, raw } = data;
        if (!details) return;

        this.info = {
            input: {
                format: details.input.format,
                type: details.input.type,
                uri: details.input.uri,
                programs: details.input.programs,
                streams: details.input.streams
            },
            video: {
                codec: {
                    name: details.video.codec.name,
                    longName: details.video.codec.long_name,
                    profile: details.video.codec.profile,
                    level: details.video.codec.level
                },
                format: {
                    width: details.video.format.width,
                    height: details.video.format.height,
                    pixFmt: details.video.format.pix_fmt,
                    displayAspectRatio: details.video.format.aspect_ratio?.display,
                    sampleAspectRatio: details.video.format.aspect_ratio?.sample,
                    fieldorder: raw.video.extended.stream.format.field_order,
                    colorRange: details.video.format.color?.range,
                    colorSpace: details.video.format.color?.space
                },
                encoding: {
                    hasBFrames: details.video.encoding.has_b_frames,
                    refs: details.video.encoding.refs,
                    extraDataSize: details.video.encoding.extradata_size
                },
                timing: {
                    avgFrameRate: details.video.timing.avg_frame_rate,
                    realFrameRate: details.video.timing.r_frame_rate,
                    timeBase: details.video.timing.time_base
                }
            },
            audio: {
                codec: {
                    name: details.audio.codec.name,
                    longName: details.audio.codec.long_name,
                    profile: details.audio.codec.profile
                },
                format: {
                    channels: details.audio.format.channels,
                    channelLayout: details.audio.format.channel_layout,
                    sampleRate: details.audio.format.sample_rate,
                    sampleFormat: details.audio.format.sample_fmt,
                    bitsPerSample: details.audio.format.bits_per_sample
                }
            },
            program: {
                id: details.program.id,
                pmtPid: details.program.pmt_pid,
                pcrPid: details.program.pcr_pid,
                bitrate: details.program.bitrate,
                streams: details.program.nb_streams,
                videopid: status.video.pid,
                audiopid: status.audio.pid
            }
        };

        this.detectChanges();
    }

    detectChanges(newData = this.info, path = '', changes = []) {
        if (!newData) return changes;

        Object.entries(newData).forEach(([key, value]) => {
            const fullPath = path ? `${path}.${key}` : key;
            const prevValue = this.previousValues.get(fullPath);

            if (value !== null && typeof value === 'object') {
                this.detectChanges(value, fullPath, changes);
            } else if (prevValue !== value) {
                this.previousValues.set(fullPath, value);
                changes.push({
                    path: fullPath,
                    oldValue: prevValue,
                    newValue: value
                });
            }
        });

        return changes;
    }

    getFieldValue(path) {
        return path.split('.').reduce((obj, key) => obj?.[key], this.info);
    }

    startUpdates() {
        this.fetchInfo();  // Initial fetch
        this.updateInterval = setInterval(() => this.fetchInfo(), 5000);
    }

    stopUpdates() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
            this.updateInterval = null;
        }
    }

    cleanup() {
        this.stopUpdates();
        this.previousValues.clear();
        this.info = null;
    }
}

export const streamInfo = new StreamInfoManager();