import { channelState, STATE_EVENTS } from './modules/state.js';
import { initializeCharts, startChartUpdates, stopChartUpdates } from './modules/charts.js';
import { streamInfo } from './modules/stream-info.js';
import { performanceStats } from './modules/performance-stats.js';
import { displayManager } from './modules/display-manager.js';
import { inputConfig } from './modules/inputconfig.js';
import { statsManager } from './modules/stats.js';

// Tab management
function initializeTabs() {
    const tabList = document.querySelector('.tab-list');
    const tabButtons = tabList.querySelectorAll('button');
    const tabPanes = document.querySelectorAll('.tab-content > div');

    console.log('Initializing tabs - buttons:', tabButtons.length, 'panes:', tabPanes.length);

    // Hide all panes except overview on initial load
    tabPanes.forEach((pane, index) => {
        if (index === 0) {
            pane.classList.add('active');
            pane.style.display = 'block';
        } else {
            pane.classList.remove('active');
            pane.style.display = 'none';
        }
    });

    // Set first button as active
    if (tabButtons.length > 0) {
        tabButtons[0].classList.add('active');
    }

    // Add click listeners to buttons
    tabButtons.forEach(button => {
        button.addEventListener('click', (e) => {
            const targetId = button.dataset.tab;
            console.log('Switching to tab:', targetId);

            // Remove active states
            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabPanes.forEach(pane => {
                pane.classList.remove('active');
                pane.style.display = 'none';
            });

            // Activate selected tab
            button.classList.add('active');
            const targetPane = document.getElementById(targetId);
            if (targetPane) {
                targetPane.classList.add('active');
                targetPane.style.display = 'block';

                // Initialize specific tab content
                initializeTabContent(targetId);
            }
        });
    });
}

// Tab content initialization
function initializeTabContent(tabId) {
    switch (tabId) {
        case 'overview':
            startChartUpdates();
            break;
        case 'input':
            stopChartUpdates(); // Stop chart updates when leaving overview
            inputConfig.init();
            break;
        case 'transcoder':
            stopChartUpdates();
            // Initialize transcoder tab if needed
            break;
        case 'output':
            stopChartUpdates();
            // Initialize output tab if needed
            break;
        case 'diagnostic':
            stopChartUpdates();
            // Initialize diagnostic tab if needed
            break;
    }
}

// Channel configuration
async function loadChannelConfig() {
    try {
        const response = await fetch(`http://${channelState.server}:5000/api/channels`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        const config = data.channels.find(c => c.name === channelState.name);
        if (config) {
            channelState.setConfig(config);
        } else {
            console.error('Channel config not found');
        }
    } catch (error) {
        console.error('Error loading channel config:', error);
        displayManager.showError('Failed to load channel configuration');
    }
}

// Page initialization
async function initializePage() {
    console.log('Initializing page...');

    if (!channelState.name) {
        console.error('No channel name provided');
        displayManager.showError('Channel name is required');
        return;
    }

    // Set channel name
    const channelNameElement = document.getElementById('channel-name');
    if (channelNameElement) {
        channelNameElement.textContent = channelState.name;
    }

    // Set server
    const server = localStorage.getItem('selectedServer') || '192.168.110.42';
    channelState.setServer(server);

    try {
        // Initialize components
        initializeCharts();
        await loadChannelConfig();
        startChartUpdates();

        console.log('Page initialization complete');
    } catch (error) {
        console.error('Error during page initialization:', error);
        displayManager.showError('Failed to initialize page components');
    }
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, starting initialization');
    initializeTabs();
    initializePage();
});

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        const activeTab = document.querySelector('.tab-pane.active');
        if (activeTab) {
            initializeTabContent(activeTab.id);
        }
    } else {
        stopChartUpdates();
    }
});

// Error handling for unhandled promises
window.addEventListener('unhandledrejection', (event) => {
    console.error('Unhandled promise rejection:', event.reason);
    displayManager.showError('An unexpected error occurred');
});