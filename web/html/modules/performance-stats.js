// performance-stats.js
import { channelState, INPUT_TYPES } from './state.js';
import { fetchStats } from './api.js';

class PerformanceManager {
    constructor() {
        this.updateInterval = null;
        this.stats = null;
    }

    async fetchStats() {
        if (!channelState.server || !channelState.name) return null;

        try {
            const [inputStats, encoderStats, outputStats] = await Promise.all([
                this.fetchInputStats(),
                this.fetchTranscoderStats(),
                this.fetchOutputStats()
            ]);

            this.stats = {
                input: inputStats,
                transcoder: encoderStats,
                outputs: outputStats
            };

            return this.stats;
        } catch (error) {
            console.error('Error fetching performance stats:', error);
            return null;
        }
    }

    async fetchInputStats() {
        const inputType = channelState.getInputType();
        if (!inputType) return null;

        const stats = await fetchStats(channelState.server, channelState.name, inputType);
        if (!stats?.length) return null;

        const latestStats = stats[stats.length - 1].stats;
        
        switch (inputType) {
            case INPUT_TYPES.SRT:
                return this.processSRTStats(latestStats);
            case INPUT_TYPES.UDP:
                return this.processUDPStats(latestStats);
            case INPUT_TYPES.HLS:
                return this.processHLSStats(latestStats);
            default:
                return null;
        }
    }

    processSRTStats(stats) {
        if (!stats) return null;
        return {
            bitrate: stats['bandwidth-mbps'],
            packetsReceived: stats['packets-received'],
            packetsLost: stats['packets-received-lost'],
            packetsRetransmitted: stats['packets-retransmitted'],
            latencyset: stats['negotiated-latency-ms'],
            rttMs: stats['rtt-ms'],
            bytesReceived: stats['bytes-received'],
            bytesLost: stats['bytes-received-lost'],
            bytesRetransmitted: stats['bytes-retransmitted'],
            lossRate: (stats['packets-lost'] / stats['packets-received'] * 100) || 0
        };
    }

    processUDPStats(stats) {
        if (!stats) return null;
        return {
            bitrate: stats.bitrate_mbps,
            packetsReceived: stats.packets_received,
            bytesReceived: stats.bytes_received,
            bufferLevel: stats.buffer_level,
            bufferSize: stats.buffer_size
        };
    }

    processHLSStats(stats) {
        if (!stats) return null;
        return {
            bitrate: stats.bitrate_mbps,
            bufferSize: stats.buffer_level_bytes,
            bufferDuration: stats.buffer_level_time,
            bytesReceived: stats.bytes_received,
            
        };
    }

    async fetchTranscoderStats() {
        if (channelState.isPassthrough()) return null;

        const [videoStats, audioStats] = await Promise.all([
            fetchStats(channelState.server, channelState.name, 'video_encoder_output'),
            fetchStats(channelState.server, channelState.name, 'audio_encoder_output')
        ]);

        return {
            video: videoStats?.length ? this.processVideoEncoderStats(videoStats[videoStats.length - 1].stats) : null,
            audio: audioStats?.length ? this.processAudioEncoderStats(audioStats[audioStats.length - 1].stats) : null
        };
    }

    processVideoEncoderStats(stats) {
        if (!stats) return null;
        return {
            bitrate: stats.output_bitrate_kbps / 1000,
            fps: stats.output_fps,
            framesEncoded: stats.frame_count,
            width: stats.output_width,
            height: stats.output_height
        };
    }

    processAudioEncoderStats(stats) {
        if (!stats) return null;
        return {
            bitrate: stats.output_bitrate_kbps / 1000,
            sampleRate: stats.sample_rate,
            channels: stats.channels,
            bufferLevel: stats.buffer_level
        };
    }

    async fetchOutputStats() {
        const outputs = channelState.config?.outputs || [];
        const outputStats = {};

        await Promise.all(outputs.map(async (output, index) => {
            const stats = await fetchStats(
                channelState.server,
                channelState.name,
                `udp_output_${index}`
            );

            if (stats?.length) {
                outputStats[index] = this.processOutputStats(
                    stats[stats.length - 1].stats,
                    output.type
                );
            }
        }));

        return outputStats;
    }

    processOutputStats(stats, outputType) {
        if (!stats) return null;

        const baseStats = {
            bitrate: stats.bitrate_mbps,
            bytesSent: stats.bytes_served,
            buffersSent: stats.rendered_buffers
        };

        if (outputType === 'udpsink') {
            return {
                ...baseStats,
                packetsSent: stats.packets_sent,
                bufferLevel: stats.buffer_level
            };
        } else if (outputType === 'srtsink') {
            return {
                ...baseStats,
                rttMs: stats.rtt_ms,
                lossRate: stats.loss_percentage
            };
        }

        return baseStats;
    }

    startUpdates() {
        this.fetchStats();  // Initial fetch
        this.updateInterval = setInterval(() => this.fetchStats(), 1000);
    }

    stopUpdates() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
            this.updateInterval = null;
        }
    }

    cleanup() {
        this.stopUpdates();
        this.stats = null;
    }
}

export const performanceStats = new PerformanceManager();