// display-manager.js
 import { streamInfo } from './stream-info.js';
 import { performanceStats } from './performance-stats.js';


class DisplayManager {
    constructor() {
        this.fields = new Map();
        this.configChanges = new Set();
        this.performanceChanges = new Set();
        this.updateInterval = null;
    }

    initialize() {
        this.initializeFields();
        this.setupListeners();
        this.startUpdates();
    }

    initializeFields() {
        document.querySelectorAll('[data-field]').forEach(element => {
            this.fields.set(element.dataset.field, {
                element,
                type: element.dataset.type || 'text',
                isConfig: element.dataset.config === 'true',
                formatOptions: element.dataset.format ? JSON.parse(element.dataset.format) : {}
            });
        });
    }

    async update() {
        try {
            const [configInfo, perfStats] = await Promise.all([
                streamInfo.fetchInfo(),
                performanceStats.fetchStats(),
                
            ]);

            this.updateDisplay(configInfo, perfStats);
            // console.log(streamInfo.fetchStatus());
              
               const obj = await streamInfo.fetchStatus();
               const value = obj.uptime;
             
               this.updateTimestamp(value);
        } catch (error) {
            console.error('Error updating display:', error);
        }
    }





// In display-manager.js

updatePerformanceMetrics(perfStats) {
    const metricsContainer = document.getElementById('performance-metrics');
    if (!metricsContainer) return;

    let html = '';

    // Input metrics based on type
    if (perfStats?.input) {
        html += `
            <div class="stat-group">
                <div class="stat-item">
                    <span class="stat-label">Current Bitrate</span>
                    <span class="stat-value">${this.formatValue(perfStats.input.bitrate, 'bitrate')}</span>
                </div>
                ${perfStats.input.packetsReceived ? `
                    <div class="stat-item">
                        <span class="stat-label">Packets Received</span>
                        <span class="stat-value">${this.formatValue(perfStats.input.packetsReceived, 'count')}</span>
                    </div>
                ` : ''}
                ${perfStats.input.rttMs ? `
                    <div class="stat-item">
                        <span class="stat-label">RTT</span>
                        <span class="stat-value">${this.formatValue(perfStats.input.rttMs, 'time')}</span>
                    </div>
                ` : ''}
            </div>
        `;
    }

    // Transcoder metrics if available
    if (perfStats?.transcoder) {
        html += `
            <div class="stat-group">
                <div class="stat-item">
                    <span class="stat-label">Output Bitrate</span>
                    <span class="stat-value">${this.formatValue(perfStats.transcoder.video?.bitrate, 'bitrate')}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Frame Rate</span>
                    <span class="stat-value">${this.formatValue(perfStats.transcoder.video?.fps, 'number')} fps</span>
                </div>
            </div>
        `;
    }

    // Output metrics
    if (perfStats?.outputs) {
        Object.entries(perfStats.outputs).forEach(([index, stats]) => {
            html += `
                <div class="stat-group">
                    <h4>Output ${parseInt(index) + 1}</h4>
                    <div class="stat-item">
                        <span class="stat-label">Bitrate</span>
                        <span class="stat-value">${this.formatValue(stats.bitrate, 'bitrate')}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-label">Buffer Level</span>
                        <span class="stat-value">${this.formatValue(stats.bufferLevel, 'percentage')}</span>
                    </div>
                </div>
            `;
        });
    }

    metricsContainer.innerHTML = html;
}


    updateDisplay(configInfo, perfStats) {
        this.fields.forEach((field, path) => {
            try {
                let value;
                if (path.startsWith('perf.')) {
                    value = this.getNestedValue(perfStats, path.substring(5));
                } else {
                    value = this.getNestedValue(configInfo, path);
                }

                const formattedValue = this.formatValue(value, field.type, field.formatOptions);
                
                if (field.element.textContent !== formattedValue) {
                    field.element.textContent = formattedValue;
                    this.highlightChange(field.element, field.isConfig);
                }
            } catch (error) {
                console.warn(`Error updating field ${path}:`, error);
                field.element.textContent = '-';
            }
        });
    this.updatePerformanceMetrics(perfStats);
    }

    formatValue(value, type, options = {}) {
        if (value === undefined || value === null) return '-';

        switch (type) {
            case 'bitrate':
                return this.formatBitrate(value);
            case 'bytes':
                return this.formatBytes(value);
            case 'framerate':
                return this.formatFrameRate(value);
            case 'resolution':
                return `${value.width}x${value.height}`;
            case 'time':
                return this.formatTime(value);
            case 'percentage':
                return `${value.toFixed(1)}%`;
            case 'hex':
                return `0x${value.toString(16).padStart(4, '0')}`;
            case 'pidas':
                if (value == 'auto') {
                return String(value);
                 }
                else{
                return parseInt(value, 16);
                }
            case 'boolean':
                return value ? 'Yes' : 'No';
            case 'pid':
                return value ? `0x${value.toString(16).toUpperCase().padStart(4, '0')}` : '-';
            case 'khz':
                return `${(value / 1000).toFixed(1)} kHz`;
            case 'count':
                return value.toLocaleString();
            default:
                return String(value);
        }
    }

    formatBitrate(value) {
        if (value >= 1000) {
            return `${(value / 1000).toFixed(2)} Gbps`;
        }
        return `${value.toFixed(2)} Mbps`;
    }

    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
    }

    formatFrameRate(value) {
        if (typeof value === 'string' && value.includes('/')) {
            const [num, den] = value.split('/').map(Number);
            return `${(num / den).toFixed(2)} fps`;
        }
        return `${Number(value).toFixed(2)} fps`;
    }

    formatTime(value) {
        if (value < 1000) {
            return `${value.toFixed(0)}ms`;
        }
        return `${(value / 1000).toFixed(2)}s`;
    }

    getNestedValue(obj, path) {
        return path.split('.').reduce((acc, part) => {
            if (acc === null || acc === undefined) return null;
            return acc[part];
        }, obj);
    }

    highlightChange(element, isConfig) {
        const changeClass = isConfig ? 'config-change' : 'performance-change';
        element.classList.add(changeClass);
        setTimeout(() => element.classList.remove(changeClass), 1000);
    }

  


    updateTimestamp(status) {
        const timestampElement = document.getElementById('stats-update-time');
        // console.log(status);
        if (timestampElement && status) {

            if (!status) return 'N/A';
    
    // Convert to number if it's a string
    const seconds = parseInt(status);
    
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainingSeconds = seconds % 60;

    const parts = [];
    if (days > 0) parts.push(`${days}d`);
    if (hours > 0) parts.push(`${hours}h`);
    if (minutes > 0) parts.push(`${minutes}m`);
    parts.push(`${remainingSeconds}s`);

    status = parts.join(' ');


            timestampElement.textContent = `Uptime: ${status}`;
        }
    }

    setupListeners() {
        // Add click handlers for expandable sections
        document.querySelectorAll('.stats-section-header').forEach(header => {
            header.addEventListener('click', () => {
                const section = header.closest('.stats-section');
                section.classList.toggle('expanded');
            });
        });

        // Handle visibility changes
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.startUpdates();
            } else {
                this.stopUpdates();
            }
        });
    }

    startUpdates() {
        this.update(); // Initial update
        this.updateInterval = setInterval(() => this.update(), 1000);
    }

    stopUpdates() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
            this.updateInterval = null;
        }
    }

    cleanup() {
        this.stopUpdates();
        this.fields.clear();
        this.configChanges.clear();
        this.performanceChanges.clear();
    }
}

// Create and export the display manager instance
export const displayManager = new DisplayManager();

// Add CSS for transitions and highlighting
const styles = `
    .stat-value {
        transition: color 0.3s ease, background-color 0.3s ease;
    }
    
    .config-change {
        color: var(--primary-color);
        background-color: rgba(255, 0, 0, 0.1);
    }
    
    .performance-change {
        color: #2ecc71;
        background-color: rgba(46, 204, 113, 0.1);
    }

    .stats-section {
        transition: max-height 0.3s ease;
        overflow: hidden;
    }

    .stats-section:not(.expanded) .stats-content {
        display: none;
    }

    .stats-section-header {
        cursor: pointer;
        user-select: none;
    }

    .stats-section-header:hover {
        background-color: rgba(0, 0, 0, 0.05);
    }
`;

// Add the styles to the document
const styleSheet = document.createElement('style');
styleSheet.textContent = styles;
document.head.appendChild(styleSheet);

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    displayManager.initialize();
});