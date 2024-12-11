// channel-overview.js
import { channelState, STATE_EVENTS } from './modules/state.js';
import { initializeCharts, startChartUpdates, stopChartUpdates } from './modules/charts.js';

import { displayManager } from './modules/display-manager.js';

// import { streamInfo } from './modules/stream-info.js';
// import { performanceStats } from './modules/performance-stats.js';

async function loadChannelConfig() {
    try {
        const response = await fetch(`http://${channelState.server}:5000/api/channels`);
        const data = await response.json();
        const config = data.channels.find(c => c.name === channelState.name);
        channelState.setConfig(config);
    } catch (error) {
        console.error('Error loading channel config:', error);
    }
}


function log(message) {
    console.log(`[${new Date().toISOString()}] ${message}`);
}


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
            
        }

        

        

    } catch (error) {
        log(`Error initializing channels section: ${error.message}`);
    }
}


async function initializePage() {
    if (!channelState.name) {
        const params = new URLSearchParams(window.location.search);
        const channelName = params.get('name');
        if (!channelName) {
            console.error('No channel name provided');
            return;
        }
        channelState.name = channelName;
    }

    // Set channel name in header
    document.getElementById('channel-name').textContent = channelState.name;

    
    // Initialize collapsible stats sections
    initializeStatsCollapsible();
    

    // Update navigation links with channel name
    document.querySelectorAll('.channel-nav a').forEach(link => {
        const href = link.getAttribute('href');
        if (href.includes('channel-')) {
            link.href = `${href}?name=${channelState.name}`;
        }
    });

    // Get server from local storage or default to first server
   // const server = localStorage.getItem('selectedServer') || '192.168.110.42';
   // channelState.setServer(server);

// Get server IP from getServerIP() function
    const serverIP = await getServerIP();
    channelState.setServer(serverIP);

    // Initialize charts
    initializeCharts();
    
    // Load initial config
    await loadChannelConfig();
    
    // Start updates
    startChartUpdates();
}


// Handle visibility changes
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        startChartUpdates();
    } else {
        stopChartUpdates();
    }
});

// Add this new function
function initializeStatsCollapsible() {
    // Add initial collapsed state to all sections
    document.querySelectorAll('.stats-section').forEach(section => {
        section.classList.add('collapsed');
    });

    // Add click handlers for section headers
    document.querySelectorAll('.stats-section-header').forEach(header => {
        header.addEventListener('click', () => {
            header.closest('.stats-section').classList.toggle('collapsed');
        });
    });
}


// Initialize when DOM is loaded
// document.addEventListener('DOMContentLoaded', initializePage);



document.addEventListener('DOMContentLoaded', async () => {
     await initializeChannelsSection();
     await initializePage();
   
});
