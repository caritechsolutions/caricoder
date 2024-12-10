// state.js
export const STATE_EVENTS = {
    CONFIG_LOADED: 'configLoaded',
    SERVER_CHANGED: 'serverChanged',
    CHARTS_INITIALIZED: 'chartsInitialized'
};


export const INPUT_TYPES = {
    SRT: 'srt_input',
    UDP: 'udp_input',
    HLS: 'hls_input'
};

class ChannelState {

    constructor() {
        this.name = '';
        this.server = '';
        this.config = null;
        this.charts = {};
        this.updateInterval = null;
        this.eventListeners = new Map();
        
        const urlParams = new URLSearchParams(window.location.search);
        this.name = urlParams.get('name') || '';
    }

    getInputType() {
        if (!this.config?.inputs?.[0]?.type) return null;
        const inputType = this.config.inputs[0].type.toLowerCase();
        
        if (inputType.includes('srt')) return INPUT_TYPES.SRT;
        if (inputType.includes('udp')) return INPUT_TYPES.UDP;
        if (inputType.includes('hls')) return INPUT_TYPES.HLS;
        
        return null;
    }

    // Add this method
    isPassthrough() {
        const transcoding = this.config?.transcoding || {};
        
        // Check video passthrough
        const videoStreams = transcoding.video?.streams || [];
        const videoPassthrough = videoStreams.length > 0 
            ? videoStreams.every(stream => stream.codec === 'passthrough')
            : transcoding.video?.codec === 'passthrough';
            
        // Check audio passthrough
        const audioPassthrough = transcoding.audio?.codec === 'passthrough';
        
        return videoPassthrough && audioPassthrough;
    }

    addEventListener(event, callback) {
        if (!this.eventListeners.has(event)) {
            this.eventListeners.set(event, new Set());
        }
        this.eventListeners.get(event).add(callback);
    }

    removeEventListener(event, callback) {
        const listeners = this.eventListeners.get(event);
        if (listeners) {
            listeners.delete(callback);
        }
    }

    emitEvent(event, data) {
        const listeners = this.eventListeners.get(event);
        if (listeners) {
            listeners.forEach(callback => callback(data));
        }
    }

    setServer(server) {
        this.server = server;
        this.emitEvent(STATE_EVENTS.SERVER_CHANGED, server);
    }

    setConfig(config) {
        this.config = config;
        this.emitEvent(STATE_EVENTS.CONFIG_LOADED, config);
    }

    setChart(name, chart) {
        this.charts[name] = chart;
        if (Object.keys(this.charts).length === 2) {
            this.emitEvent(STATE_EVENTS.CHARTS_INITIALIZED, this.charts);
        }
    }

    cleanup() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
            this.updateInterval = null;
        }
        this.eventListeners.clear();
    }
}

export const channelState = new ChannelState();