// channels.js - Channel Management for CariCoder Scheduler

//===================================
// State Management 
//===================================

const channelCache = {
    _data: new Map(),
    
    getChannel(channelName) {
        return this._data.get(channelName);
    },
    
    setChannel(channelName, data) {
        this._data.set(channelName, data);
    },
    
    removeChannel(channelName) {
        this._data.delete(channelName);
    },
    
    getAllChannels() {
        return Array.from(this._data.keys());
    }
};

//===================================
// Utility Functions
//===================================

function log(message) {
    console.log(`[${new Date().toISOString()}] ${message}`);
}

function getUrlParameter(name) {
    try {
        name = name.replace(/[\[]/, '\\[').replace(/[\]]/, '\\]');
        var regex = new RegExp('[\\?&]' + name + '=([^&#]*)');
        var results = regex.exec(location.search);
        return results === null ? '' : decodeURIComponent(results[1].replace(/\+/g, ' '));
    } catch (error) {
        log(`Error getting URL parameter: ${error.message}`);
        return '';
    }
}

function formatBitrate(mbps) {
    if (!mbps) return '0 Mbps';
    return `${mbps.toFixed(2)} Mbps`;
}

function formatUptime(seconds) {
    if (!seconds) return 'N/A';
    
    // Convert to number if it's a string
    seconds = parseInt(seconds);
    
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainingSeconds = seconds % 60;

    const parts = [];
    if (days > 0) parts.push(`${days}d`);
    if (hours > 0) parts.push(`${hours}h`);
    if (minutes > 0) parts.push(`${minutes}m`);
    parts.push(`${remainingSeconds}s`);

    return parts.join(' ');
}

function getInputType(channelConfig) {
    if (!channelConfig || !channelConfig.inputs || !channelConfig.inputs[0]) {
        return null;
    }
    
    const inputType = channelConfig.inputs[0].type;
    if (inputType.includes('srt')) {
        return 'srt_input';
    } else if (inputType.includes('udp')) {
        return 'udp_input';
    } else if (inputType.includes('hls')) {
        return 'hls_input';
    }
    return null;
}

// Enhanced output stats fetching
async function fetchOutputStats(serverAddress, channelName, outputIndex) {
    try {
        const response = await fetch(`http://${serverAddress}:5000/stats/live/${channelName}/udp_output_${outputIndex}`);
        if (!response.ok) return null;
        const data = await response.json();
        if (!data || data.length === 0) return null;

        const latestStats = data[data.length - 1];
        return {
            bitrate_mbps: (latestStats.stats.bitrate_mbps || 0),
            timestamp: latestStats.timestamp
        };
    } catch (error) {
        log(`Error fetching output stats: ${error.message}`);
        return null;
    }
}


// Update the fetchEncoderStats function
async function fetchEncoderStats(serverAddress, channelName) {
    try {
        const response = await fetch(`http://${serverAddress}:5000/stats/live/${channelName}/video_encoder_output`);
        if (!response.ok) return null;
        const data = await response.json();
        if (!data || data.length === 0) return null;
        return {
            ...data[data.length - 1].stats,
            timestamp: data[data.length - 1].timestamp
        };
    } catch (error) {
        log(`Error fetching encoder stats: ${error.message}`);
        return null;
    }
}


// New function to fetch input format info
async function fetchInputStats(serverAddress, channelName) {
    try {
        const response = await fetch(`http://${serverAddress}:5000/stats/live/${channelName}/video_encoder_input`);
        if (!response.ok) return null;
        const data = await response.json();
        return data.length > 0 ? data[data.length - 1].stats : null;
    } catch (error) {
        log(`Error fetching input stats: ${error.message}`);
        return null;
    }
}

function hasConfigChanged(oldConfig, newConfig) {
    const compareSection = (oldSection, newSection, fields) => {
        if (!oldSection || !newSection) return true;
        return fields.some(field => JSON.stringify(oldSection[field]) !== JSON.stringify(newSection[field]));
    };
    
    return (
        compareSection(oldConfig, newConfig, ['name']) ||
        compareSection(oldConfig.inputs?.[0], newConfig.inputs?.[0], ['uri', 'type']) ||
        compareSection(oldConfig.transcoding, newConfig.transcoding, ['video', 'audio']) ||
        compareSection(oldConfig, newConfig, ['outputs'])
    );
}

//===================================
// Data Fetching
//===================================

async function fetchServerIPs() {
    try {
        const response = await fetch('servers.json');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        return Array.isArray(data.servers) ? data.servers : [];
    } catch (error) {
        log(`Error fetching server IPs: ${error.message}`);
        return [];
    }
}

async function getServerIP() {
    try {
        const serverFromUrl = getUrlParameter('server');
        if (serverFromUrl) {
            return serverFromUrl;
        }

        const serverIPs = await fetchServerIPs();
        return serverIPs.length > 0 ? serverIPs[0] : null;
    } catch (error) {
        log(`Error getting server IP: ${error.message}`);
        return null;
    }
}

async function fetchChannelList(serverAddress) {
    try {
        const response = await fetch(`http://${serverAddress}:8001/list`);
        if (!response.ok) throw new Error('Failed to fetch channel list');
        const data = await response.json();
        log('Channel list response:', data);
        return data.channels || {};
    } catch (error) {
        log(`Error fetching channel list: ${error.message}`);
        return {};
    }
}

async function fetchChannelStatus(serverAddress) {
    try {
        const response = await fetch(`http://${serverAddress}:8001/status`);
        if (!response.ok) throw new Error('Failed to fetch channel status');
        const data = await response.json();
        console.log('Status response:', JSON.stringify(data, null, 2));  // <-- Modified log
        return data.channels || {};
    } catch (error) {
        log(`Error fetching channel status: ${error.message}`);
        return {};
    }
}

async function fetchChannelConfig(serverAddress) {
    if (!serverAddress) {
        log('No server address provided');
        return { channels: [] };
    }

    log(`Fetching channel config from server: ${serverAddress}`);
    try {
        const response = await fetch(`http://${serverAddress}:5000/api/channels`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        return data || { channels: [] };
    } catch (error) {
        log(`Error fetching channel config: ${error.message}`);
        return { channels: [] };
    }
}

// Update fetchChannelStats to handle HLS input
async function fetchChannelStats(serverAddress, channelName, channelConfig) {
    try {
        const inputType = getInputType(channelConfig);
        if (!inputType) return null;

        const response = await fetch(`http://${serverAddress}:5000/stats/live/${channelName}/${inputType}`);
        if (!response.ok) throw new Error('Failed to fetch channel stats');
        const data = await response.json();
        
        if (data && data.length > 0) {
            const latestStats = data[data.length - 1];
            
            switch(inputType) {
                case 'srt_input':
                    return {
                        bitrate: latestStats.stats['receive-rate-mbps'],
                        bandwidth: latestStats.stats['bandwidth-mbps'],
                        timestamp: latestStats.timestamp
                    };
                case 'udp_input':
                case 'hls_input':
                    return {
                        bitrate: latestStats.stats.bitrate_mbps,
                        buffer: latestStats.stats.buffer_level_bytes,
                        timestamp: latestStats.timestamp
                    };
            }
        }
        return null;
    } catch (error) {
        log(`Error fetching channel stats: ${error.message}`);
        return null;
    }
}

// For debugging purposes, let's add a function to log detailed stats
function logDetailedStats(stats, inputType) {
    if (!stats) return;
    
    const details = {
        timestamp: new Date(stats.timestamp * 1000).toISOString(),
        bitrate: `${stats.bitrate.toFixed(2)} Mbps`
    };
    
    if (inputType === 'srt_input' && stats.bandwidth) {
        details.bandwidth = `${stats.bandwidth.toFixed(2)} Mbps`;
    }
    
    log(`Channel Stats [${inputType}]:`, JSON.stringify(details, null, 2));
}

// Modify getChannelData to properly capture process information
async function getChannelData(serverAddress) {
    try {
        const configResponse = await fetchChannelConfig(serverAddress);
        const channelStatus = await fetchChannelStatus(serverAddress);
        const channelList = await fetchChannelList(serverAddress);
        const channelStats = {};

      const runningChannels = Object.entries(channelList)
    .filter(([name, info]) => info.running)
    .map(([name, info]) => {
        const status = channelStatus[name];
        return {
            name: name,
            channel: name,
            running: true,
            processes: status?.processes || {} 
        };
    });
console.log('Running channels:', runningChannels);  // Add this log

        

        for (const channel of runningChannels) {
            const channelConfig = configResponse.channels.find(c => c.name === channel.channel || c.name === channel.name);
            
            if (channelConfig) {
                // Get input stats
                const stats = await fetchChannelStats(serverAddress, channel.channel || channel.name, channelConfig);
                if (stats) {
                    channelStats[channel.channel || channel.name] = stats;
                }
                
                // Get encoder stats if transcoding
                if (!channelConfig.transcoding?.video?.codec !== 'passthrough') {
                    const encoderStats = await fetchEncoderStats(serverAddress, channel.channel || channel.name);
                    if (encoderStats) {
                        channelStats[channel.channel || channel.name].encoderStats = encoderStats;
                    }
                }
            }
        }

        return {
            config: configResponse,
            runningChannels,
            channelStats
        };
    } catch (error) {
        log(`Error getting channel data: ${error.message}`);
        return {
            config: { channels: [] },
            runningChannels: [],
            channelStats: {}
        };
    }
}

// Fix timestamp check in output bitrate polling
function pollOutputBitrates(channelName, containerElement) {
    const serverSelect = document.getElementById('server-select');
    const serverAddress = serverSelect?.value;
    if (!serverAddress) return;

    const lastUpdateTimes = new Map();

    const updateOutputBitrates = async () => {
        const outputElements = containerElement.querySelectorAll('.output-bitrate');
        const currentTime = Date.now();

        for (const element of outputElements) {
            try {
                const outputIndex = element.dataset.outputIndex;
                const stats = await fetchOutputStats(serverAddress, channelName, outputIndex);
                
                if (stats && stats.timestamp && stats.bitrate_mbps !== undefined) {
                    const statsTime = stats.timestamp * 1000;
                    if (currentTime - statsTime < 10000) {
                        lastUpdateTimes.set(outputIndex, currentTime);
                        element.textContent = formatBitrate(stats.bitrate_mbps);
                    } else {
                        element.textContent = 'No Signal (Stale)';
                    }
                } else {
                    const lastUpdate = lastUpdateTimes.get(outputIndex);
                    if (!lastUpdate || (currentTime - lastUpdate) > 10000) {
                        element.textContent = 'No Signal';
                        lastUpdateTimes.delete(outputIndex);
                    }
                }
            } catch (error) {
                log(`Error updating output ${element.dataset.outputIndex}: ${error.message}`);
            }
        }
    };

    // Initial update
    updateOutputBitrates();

    // Set up periodic updates
    const intervalId = setInterval(updateOutputBitrates, 5000);
    containerElement.dataset.bitrateIntervalId = intervalId;

    // Cleanup on container removal
    const observer = new MutationObserver((mutations) => {
        if (!document.contains(containerElement)) {
            clearInterval(intervalId);
            observer.disconnect();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    
    return intervalId;
}


//===================================
// DOM Manipulation
//===================================

function createStatusIndicator(isRunning, isBackup = false) {
    const statusClass = isBackup ? 'status-backup' : 
                       isRunning ? 'status-running' : 
                       'status-stopped';
    
    return `<span class="status-indicator ${statusClass}"></span>`;
}

function getTranscodingInfo(channelConfig) {
    const transcoding = channelConfig?.transcoding || {};
    
    const videoCodecs = transcoding.video?.streams?.length > 0 
        ? transcoding.video.streams
            .map(stream => stream.codec)
            .filter(Boolean)
        : [transcoding.video?.codec || 'Passthrough'];

    return {
        video: videoCodecs,
        audio: transcoding.audio?.codec || 'Passthrough'
    };
}

// Updated formatOutputs function to include bitrate display
function formatOutputs(outputs) {
    if (!outputs || !Array.isArray(outputs)) return 'No outputs';
    return outputs.map((output, index) => {
        let text = '';
        switch(output.type) {
            case 'udpsink': text = `UDP: ${output.host}:${output.port}`; break;
            case 'tcpserversink': text = `TCP: ${output.host}:${output.port}`; break;
            case 'rtmpsink': text = `RTMP: ${output.location}`; break;
            case 'srtsink': text = `SRT: ${output.uri}`; break;
            default: text = output.type;
        }
        return `<div class="output-line">
            <span class="output-text">${text}</span>
            <span class="output-bitrate" data-output-index="${index}">...</span>
        </div>`;
    }).join('');
}

function createChannelElements(channel, channelConfig, stats) {
    const elements = document.createElement('div');
    elements.className = 'channel-item';
    const channelName = channelConfig.name;
    const isRunning = !!channel;

    const initialUri = channelConfig.inputs?.[0]?.uri || 'N/A';
    const transcoding = getTranscodingInfo(channelConfig);
    const outputsText = formatOutputs(channelConfig.outputs);
    const pidInfo = channel?.processes?.input?.pid ? `PID: ${channel.processes.input.pid}` : '';
    const cpuInfo = channel?.processes?.input?.cpu_usage ? `CPU: ${channel.processes.input.cpu_usage}%` : '';
    const uptimeText = channel?.processes?.input?.uptime || 'N/A';
    
    const isTableView = document.getElementById('streams-channels')?.classList.contains('table-view');
    
    if (isTableView) {
    elements.innerHTML = `
        <div class="status-container">${createStatusIndicator(isRunning)}</div>
        <a href="channel-overview.html?name=${channelName}" class="channel-name">${channelName}</a>
        <div class="info-stack">
            <span class="uri" data-element="uri">${initialUri}</span>
            <span class="pid-cpu" data-element="pid-cpu">${pidInfo}</span>
            <span class="stats" data-element="bitrate">${formatBitrate(stats?.bitrate)}</span>
            <span class="uptime" data-element="uptime">${uptimeText}</span>
        </div>
        <div class="transcoding-info">
            <div class="video" data-element="videoCodec">
                ${transcoding.video.map(codec => `<div>Video:${codec}</div>`).join('')}
            </div>
            <span class="audio" data-element="audioCodec">Audio:${transcoding.audio}</span>
            
        </div>
        <div class="outputs-list" data-element="outputs">${outputsText}</div>
        <label class="switch">
            <input type="checkbox" data-element="toggle" ${isRunning ? 'checked' : ''}>
            <span class="slider round"></span>
        </label>
    `;
} else {
        elements.innerHTML = `
            <div class="channel-header">
                <div class="status-container">${createStatusIndicator(isRunning)}</div>
                <a href="channel-overview.html?name=${channelName}" class="channel-name">${channelName}</a>
                <label class="switch">
                    <input type="checkbox" data-element="toggle" ${isRunning ? 'checked' : ''}>
                    <span class="slider round"></span>
                </label>
            </div>
            <div class="channel-info">
                <div class="info-stack">
                    <span class="uri" data-element="uri">${initialUri}</span>
                    <span class="pid-cpu" data-element="pid-cpu">${pidInfo} ${cpuInfo}</span>
                    <span class="stats" data-element="bitrate">${formatBitrate(stats?.bitrate)}</span>
                    <span class="uptime" data-element="uptime">${uptimeText}</span>
                </div>
                <div class="transcoding-info">
                    <div class="video" data-element="videoCodec">
                        ${transcoding.video.map(codec => `<div>Video:${codec}</div>`).join('')}
                    </div>
                    <span class="audio" data-element="audioCodec">Audio:${transcoding.audio}</span>
                </div>
                
                <div class="outputs-list" data-element="outputs">${outputsText}</div>
            </div>
        `;
    }

    const elementRefs = {
        container: elements,
        status: elements.querySelector('.status-container'),
        uri: elements.querySelector('[data-element="uri"]'),
        pidCpu: elements.querySelector('[data-element="pid-cpu"]'),
        bitrate: elements.querySelector('[data-element="bitrate"]'),
        uptime: elements.querySelector('[data-element="uptime"]'),
        videoCodec: elements.querySelector('[data-element="videoCodec"]'),
        audioCodec: elements.querySelector('[data-element="audioCodec"]'),
        outputs: elements.querySelector('[data-element="outputs"]'),
        toggle: elements.querySelector('[data-element="toggle"]')
    };

    elementRefs.toggle.addEventListener('change', () => {
        handleChannelToggle(channelName, elementRefs.toggle.checked);
    });

    // Start polling output bitrates if channel is running
    if (isRunning) {
        pollOutputBitrates(channelName, elements);
    }

    return elementRefs;
}


// Fix updateChannelElement to properly handle process information
function updateChannelElement(channelName, newConfig, newStatus, newStats) {
    const cached = channelCache.getChannel(channelName);
    if (!cached) return null;

    const { elements, config: oldConfig, status: oldStatus } = cached;
    const serverAddress = document.getElementById('server-select')?.value;
    
    if (hasConfigChanged(oldConfig, newConfig)) {
        if (oldConfig.inputs?.[0]?.uri !== newConfig.inputs?.[0]?.uri) {
            elements.uri.textContent = newConfig.inputs?.[0]?.uri || 'N/A';
        }

        const newTranscoding = getTranscodingInfo(newConfig);
        const oldTranscoding = getTranscodingInfo(oldConfig);
        
        if (JSON.stringify(newTranscoding.video) !== JSON.stringify(oldTranscoding.video)) {
            elements.videoCodec.innerHTML = newTranscoding.video
                .map(codec => `<div>Video: ${codec}</div>`).join('');
        }
        if (newTranscoding.audio !== oldTranscoding.audio) {
            elements.audioCodec.textContent = `Audio: ${newTranscoding.audio}`;
        }

        const newOutputsText = formatOutputs(newConfig.outputs);
        if (elements.outputs.innerHTML !== newOutputsText) {
            elements.outputs.innerHTML = newOutputsText;
        }
    }

    const isRunning = newStatus?.processes?.input?.running ?? false;
    if (oldStatus?.isRunning !== isRunning) {
        elements.status.innerHTML = createStatusIndicator(isRunning);
        elements.toggle.checked = isRunning;

        if (!isRunning) {
            // Reset stats when channel stops
            elements.uptime.textContent = 'N/A';
            elements.bitrate.textContent = '0 Mbps';
            elements.pidCpu.textContent = '';
            
            // Reset all output bitrates
            const outputElements = elements.outputs.querySelectorAll('.output-bitrate');
            outputElements.forEach(element => {
                element.textContent = 'No Signal';
            });

            // Clear any existing intervals
            const oldIntervalId = elements.container.dataset.bitrateIntervalId;
            if (oldIntervalId) {
                clearInterval(parseInt(oldIntervalId));
                delete elements.container.dataset.bitrateIntervalId;
            }
        } else {
            // Start polling if channel is running
            pollOutputBitrates(channelName, elements.container);
        }
    }

    // Update process-specific information only if channel is running
    if (isRunning && newStatus?.processes?.input) {
        const inputProc = newStatus.processes.input;
        const pidText = inputProc.pid ? `PID: ${inputProc.pid}` : '';
        if (elements.pidCpu.textContent !== pidText) {
            elements.pidCpu.textContent = pidText;
        }
        
        const formattedUptime = formatUptime(inputProc.uptime);
        if (elements.uptime.textContent !== formattedUptime) {
            elements.uptime.textContent = formattedUptime;
        }

        // Update input bitrate only if we have new stats and channel is running
        const bitrate = newStats?.bitrate ?? 0;
        if (oldStatus?.bitrate !== bitrate) {
            elements.bitrate.textContent = formatBitrate(bitrate);
        }
    }

    channelCache.setChannel(channelName, {
        elements,
        config: newConfig,
        status: {
            isRunning,
            processes: newStatus?.processes,
            bitrate: newStats?.bitrate ?? 0
        }
    });

    return elements.container;
}

async function updateChannelsList() {
    const serverSelect = document.getElementById('server-select');
    const serverAddress = serverSelect?.value;
    
    if (!serverAddress) {
        log('No server selected');
        return;
    }

    const channelsContainer = document.getElementById('channels-container');
    if (!channelsContainer) {
        log('Channels container not found');
        return;
    }

    try {
        log(`Fetching data for server: ${serverAddress}`);
        const { config, runningChannels, channelStats } = await getChannelData(serverAddress);
        
        if (document.getElementById('streams-channels')?.classList.contains('table-view')) {
            if (!channelsContainer.querySelector('.table-header')) {
                const headerRow = document.createElement('div');
                headerRow.className = 'table-header';
                headerRow.innerHTML = `
                    <span>Status</span>
                    <span>Channel Name</span>
                    <span>Input</span>
                    <span>Transcoding</span>
                    <span>Outputs</span>
                    <span>Control</span>
                `;
                channelsContainer.appendChild(headerRow);
            }
        }

        if (!config.channels || config.channels.length === 0) {
            log('No channels found in config');
            channelsContainer.innerHTML = '<div class="no-channels">No channels configured</div>';
            return;
        }

        log(`Processing ${config.channels.length} channels`);
        const processedChannels = new Set();
        
        config.channels.forEach(channelConfig => {
            const channelName = channelConfig.name;
            processedChannels.add(channelName);
            
            log(`Processing channel: ${channelName}`);
            
            const runningChannel = runningChannels.find(c => 
    (c.channel === channelName) || (c.name === channelName)
);

const stats = channelStats[channelName];
const status = {
    isRunning: !!runningChannel,
    processes: runningChannel?.processes
};
console.log('Status being passed to updateChannelElement:', status);            const cached = channelCache.getChannel(channelName);

            if (!cached) {
                log(`Creating new channel element for: ${channelName}`);
                const elements = createChannelElements(runningChannel, channelConfig, stats);
                
                channelCache.setChannel(channelName, {
                    elements,
                    config: channelConfig,
                    status: {
                        isRunning: false,
                        pid: null,
                        cpu_usage_per_core: null,
                        uptime: null,
                        bitrate: 0,
                        timestamp: null
                    }
                });
                
                updateChannelElement(channelName, channelConfig, status, stats);
                
                if (elements.container) {
                    channelsContainer.appendChild(elements.container);
                }
            } else {
                log(`Updating existing channel: ${channelName}`);
                updateChannelElement(channelName, channelConfig, status, stats);
            }
        });

        channelCache.getAllChannels().forEach(channelName => {
            if (!processedChannels.has(channelName)) {
                log(`Removing channel from display: ${channelName}`);
                const cached = channelCache.getChannel(channelName);
                if (cached?.elements.container) {
                    cached.elements.container.remove();
                }
                channelCache.removeChannel(channelName);
            }
        });

    } catch (error) {
        log(`Error updating channels list: ${error.message}`);
        channelsContainer.innerHTML = '<div class="error">Error loading channels</div>';
    }
}

//===================================
// Channel Management
//===================================

async function handleChannelToggle(channelName, shouldStart) {
    const serverSelect = document.getElementById('server-select');
    const serverAddress = serverSelect?.value;
    
    if (!serverAddress) {
        alert('No server selected');
        return;
    }

    try {
        const response = await fetch(`http://${serverAddress}:8001/${shouldStart ? 'start' : 'stop'}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel: channelName,
                source_index: 0
            })
        });

        const result = await response.json();
        if (!response.ok || result.status === 'error') {
            throw new Error(result.message || `Failed to ${shouldStart ? 'start' : 'stop'} channel`);
        }

        await updateChannelsList();
    } catch (error) {
        log(`Error toggling channel: ${error.message}`);
        alert(`Failed to ${shouldStart ? 'start' : 'stop'} channel ${channelName}: ${error.message}`);
        
        // Revert toggle state in UI
        const cached = channelCache.getChannel(channelName);
        if (cached?.elements.toggle) {
            cached.elements.toggle.checked = !shouldStart;
        }
    }
}

//===================================
// View Toggle
//===================================

function initializeViewToggle() {
    const gridBtn = document.getElementById('grid-view-btn');
    const tableBtn = document.getElementById('table-view-btn');
    const streamsSection = document.getElementById('streams-channels');
    const channelsContainer = document.getElementById('channels-container');

    if (!streamsSection || !gridBtn || !tableBtn) return;

    streamsSection.classList.add('grid-view');

    gridBtn.addEventListener('click', () => {
        streamsSection.classList.remove('table-view');
        streamsSection.classList.add('grid-view');
        gridBtn.classList.add('active');
        tableBtn.classList.remove('active');
        localStorage.setItem('channelsViewPreference', 'grid');
        
        channelCache._data.clear();
        channelsContainer.innerHTML = '';
        updateChannelsList();
    });

    tableBtn.addEventListener('click', () => {
        streamsSection.classList.remove('grid-view');
        streamsSection.classList.add('table-view');
        tableBtn.classList.add('active');
        gridBtn.classList.remove('active');
        localStorage.setItem('channelsViewPreference', 'table');
        
        channelCache._data.clear();
        channelsContainer.innerHTML = '';
        updateChannelsList();
    });

    const savedView = localStorage.getItem('channelsViewPreference');
    if (savedView === 'table') {
        tableBtn.click();
    }
}

//===================================
// Initialization
//===================================

async function initializeChannelsSection() {
    try {
        const serverSelect = document.getElementById('server-select');
        if (!serverSelect) return;

        const serverIPs = await fetchServerIPs();
        serverSelect.innerHTML = '<option value="">Select Server</option>';
        serverIPs.forEach(ip => {
            const option = document.createElement('option');
            option.value = ip;
            option.textContent = ip;
            serverSelect.appendChild(option);
        });

        const serverIP = await getServerIP();
        if (serverIP) {
            serverSelect.value = serverIP;
            await updateChannelsList();
        }

        serverSelect.addEventListener('change', updateChannelsList);

        setInterval(() => {
            if (document.visibilityState === 'visible') {
                updateChannelsList();
            }
        }, 5000);

    } catch (error) {
        log(`Error initializing channels section: ${error.message}`);
    }
}

//===================================
// Event Listeners
//===================================

document.addEventListener('DOMContentLoaded', async () => {
    await initializeViewToggle();
    await initializeChannelsSection();
});

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        updateChannelsList();
    }
});

window.addEventListener('unhandledrejection', (event) => {
    log(`Unhandled promise rejection: ${event.reason}`);
});

window.addEventListener('error', (event) => {
    log(`Global error: ${event.message}`);
});