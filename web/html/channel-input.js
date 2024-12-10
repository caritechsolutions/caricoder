// channel-input.js

import { channelState, STATE_EVENTS } from './modules/state.js';
import { fetchServerList } from './modules/api.js';

class InputTypeSelector {
    constructor(onSelect) {
        this.onSelect = onSelect;
        this.dialog = null;
        this.createDialog();
    }

    createDialog() {
        const dialog = document.createElement('dialog');
        dialog.className = 'input-type-dialog';
        
        dialog.innerHTML = `
            <div class="dialog-content">
                <h3>Select Input Type</h3>
                <div class="input-type-grid">
                    <button class="input-type-option" data-type="srtsrc">
                        <div class="option-icon">SRT</div>
                        <span class="option-label">SRT Input</span>
                        <span class="option-description">Secure Reliable Transport protocol</span>
                    </button>
                    <button class="input-type-option" data-type="udpsrc">
                        <div class="option-icon">UDP</div>
                        <span class="option-label">UDP Input</span>
                        <span class="option-description">User Datagram Protocol</span>
                    </button>
                    <button class="input-type-option" data-type="hlssrc">
                        <div class="option-icon">HLS</div>
                        <span class="option-label">HLS Input</span>
                        <span class="option-description">HTTP Live Streaming</span>
                    </button>
                    <button class="input-type-option" data-type="rtspsrc">
                        <div class="option-icon">RTSP</div>
                        <span class="option-label">RTSP Input</span>
                        <span class="option-description">Real Time Streaming Protocol</span>
                    </button>
                </div>
                <div class="dialog-actions">
                    <button class="btn btn-secondary close-dialog">Cancel</button>
                </div>
            </div>
        `;

        dialog.querySelectorAll('.input-type-option').forEach(button => {
            button.addEventListener('click', () => {
                const type = button.dataset.type;
                this.onSelect(type);
                dialog.close();
            });
        });

        dialog.querySelector('.close-dialog').addEventListener('click', () => {
            dialog.close();
        });

        dialog.addEventListener('click', (e) => {
            if (e.target === dialog) {
                dialog.close();
            }
        });

        document.body.appendChild(dialog);
        this.dialog = dialog;
    }

    show() {
        if (this.dialog) {
            this.dialog.showModal();
        }
    }
}

class InputConfig {
    constructor() {
        this.hasUnsavedChanges = false;
        this.currentInputs = [];
        this.serverAddress = localStorage.getItem('selectedServer') || '192.168.110.42';
        this.channelName = new URLSearchParams(window.location.search).get('name');
        this.inputTypeSelector = new InputTypeSelector(this.handleInputTypeSelection.bind(this));
    }

    async init() {
        console.log('Input Config Initializing...');
        await this.initializeServerSelect();
        this.attachEventListeners();
        await this.loadInputConfig();

        if (!channelState.name) {
            const params = new URLSearchParams(window.location.search);
            const channelName = params.get('name');
            if (!channelName) {
                console.error('No channel name provided');
                return;
            }
            channelState.name = channelName;
        }

        document.getElementById('channel-name').textContent = channelState.name;

        document.querySelectorAll('.channel-nav a').forEach(link => {
            const href = link.getAttribute('href');
            if (href.includes('channel-')) {
                link.href = `${href}?name=${channelState.name}`;
            }
        });
    }

    async initializeServerSelect() {
        const serverSelect = document.getElementById('server-select');
        if (!serverSelect) return;

        try {
            const servers = await fetchServerList();
            serverSelect.innerHTML = servers.map(server => 
                `<option value="${server}" ${server === this.serverAddress ? 'selected' : ''}>${server}</option>`
            ).join('');

            serverSelect.addEventListener('change', (e) => {
                this.serverAddress = e.target.value;
                localStorage.setItem('selectedServer', this.serverAddress);
                this.loadInputConfig();
            });
        } catch (error) {
            console.error('Error loading server list:', error);
        }
    }

    async probeStream(url) {
        try {
            const response = await fetch(`http://${this.serverAddress}:5000/probe`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ url })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('Error probing stream:', error);
            throw error;
        }
    }

    renderPIDOptions(input) {
        return `
            <div class="settings-group">
                <h4>Program and PID Settings</h4>
                <div class="options-grid">
                    <div class="option-group">
                        <label>Program Number:</label>
                        <input type="text" 
                               class="option-program-number"
                               value="${input.demux?.['program-number'] || ''}"
                               placeholder="Program number">
                    </div>
                    <div class="option-group">
                        <label>Video PID:</label>
                        <input type="text" 
                               class="option-video-pid"
                               value="${input.demux?.['video-pid'] || ''}"
                               placeholder="Video PID">
                    </div>
                    <div class="option-group">
                        <label>Audio PID:</label>
                        <input type="text" 
                               class="option-audio-pid"
                               value="${input.demux?.['audio-pid'] || ''}"
                               placeholder="Audio PID">
                    </div>
                </div>
            </div>
        `;
    }

    renderInputOptions(input, index) {
        let typeSpecificOptions = '';
        switch (input.type) {
            case 'srtsrc':
                typeSpecificOptions = `
                    <div class="settings-group">
                        <h4>SRT Settings</h4>
                        <div class="options-grid">
                            <div class="option-group">
                                <label>Latency (ms):</label>
                                <input type="number" 
                                       class="option-latency"
                                       value="${input.options?.latency || 1000}"
                                       data-index="${index}">
                            </div>
                            <div class="option-group">
                                <label>Mode:</label>
                                <select class="option-mode" data-index="${index}">
                                    <option value="caller" ${input.options?.mode === 'caller' ? 'selected' : ''}>Caller</option>
                                    <option value="listener" ${input.options?.mode === 'listener' ? 'selected' : ''}>Listener</option>
                                    <option value="rendezvous" ${input.options?.mode === 'rendezvous' ? 'selected' : ''}>Rendezvous</option>
                                </select>
                            </div>
                            <div class="option-group">
                                <label>Stream ID:</label>
                                <input type="text" 
                                       class="option-streamid"
                                       value="${input.options?.streamid || ''}"
                                       placeholder="Optional stream identifier"
                                       data-index="${index}">
                            </div>
                            <div class="option-group">
                                <label>Poll Timeout (ms):</label>
                                <input type="number"
                                       class="option-timeout"
                                       value="${input.options?.['poll-timeout'] || 1000}"
                                       data-index="${index}">
                            </div>
                            <div class="option-group">
                                <label>Passphrase:</label>
                                <input type="password" 
                                       class="option-passphrase"
                                       value="${input.options?.passphrase || ''}"
                                       placeholder="Optional encryption passphrase"
                                       data-index="${index}">
                            </div>
                        </div>
                        <div class="checkbox-options">
                            <label>
                                <input type="checkbox" 
                                       class="option-wait-connection"
                                       ${input.options?.['wait-for-connection'] ? 'checked' : ''}
                                       data-index="${index}">
                                Wait for connection
                            </label>
                            <label>
                                <input type="checkbox" 
                                       class="option-auto-reconnect"
                                       ${input.options?.['auto-reconnect'] ? 'checked' : ''}
                                       data-index="${index}">
                                Auto reconnect
                            </label>
                        </div>
                    </div>
                `;
                break;
            case 'udpsrc':
                typeSpecificOptions = `
                    <div class="settings-group">
                        <h4>UDP Settings</h4>
                        <div class="options-grid">
                            <div class="option-group">
                                <label>Buffer Size (bytes):</label>
                                <input type="number" 
                                       class="option-buffer-size"
                                       value="${input.options?.buffer_size || 2097152}"
                                       data-index="${index}">
                            </div>
                            <div class="option-group">
                                <label>Interface:</label>
                                <input type="text" 
                                       class="option-interface"
                                       value="${input.options?.interface || ''}"
                                       placeholder="Network interface"
                                       data-index="${index}">
                            </div>
                        </div>
                        <div class="checkbox-options">
                            <label>
                                <input type="checkbox" 
                                       class="option-do-timestamp"
                                       ${input.options?.['do-timestamp'] ? 'checked' : ''}
                                       data-index="${index}">
                                Enable Timestamping
                            </label>
                        </div>
                    </div>
                `;
                break;
            case 'hlssrc':
                typeSpecificOptions = `
                    <div class="settings-group">
                        <h4>HLS Settings</h4>
                        <div class="options-grid">
                            <div class="option-group">
                                <label>Timeout (seconds):</label>
                                <input type="number" 
                                       class="option-timeout"
                                       value="${input.options?.timeout || 10}"
                                       data-index="${index}">
                            </div>
                            <div class="option-group">
                                <label>Retries:</label>
                                <input type="number" 
                                       class="option-retries"
                                       value="${input.options?.retries || 3}"
                                       data-index="${index}">
                            </div>
                        </div>
                    </div>
                `;
                break;
            case 'rtspsrc':
                typeSpecificOptions = `
                    <div class="settings-group">
                        <h4>RTSP Settings</h4>
                        <div class="options-grid">
                            <div class="option-group">
                                <label>Latency (ms):</label>
                                <input type="number" 
                                       class="option-latency"
                                       value="${input.options?.latency || 2000}"
                                       data-index="${index}">
                            </div>
                            <div class="option-group">
                                <label>Retry Delay (seconds):</label>
                                <input type="number" 
                                       class="option-retry-delay"
                                       value="${input.options?.['retry-delay'] || 5}"
                                       data-index="${index}">
                            </div>
                        </div>
                    </div>
                `;
                break;
        }
        
        return `
            ${this.renderPIDOptions(input)}
            ${typeSpecificOptions}
        `;
    }

    attachEventListeners() {
        document.getElementById('add-input-btn')?.addEventListener('click', () => {
            this.inputTypeSelector.show();
        });

        document.getElementById('save-changes-btn')?.addEventListener('click', () => {
            this.saveChanges();
        });
    }

    attachInputEventListeners(element, index) {
        element.querySelector('.toggle-btn')?.addEventListener('click', () => this.toggleDetails(index));
        element.querySelector('.delete-btn')?.addEventListener('click', () => this.removeInput(index));
        element.querySelector('.input-uri')?.addEventListener('change', (e) => this.handleInputChange(index, 'uri', e.target.value));
        element.querySelector('.probe-btn')?.addEventListener('click', async () => {
            const uri = element.querySelector('.input-uri').value;
            if (!uri) {
                this.showError('Please enter a URL first');
                return;
            }

            try {
                const probeData = await this.probeStream(uri);
                this.updatePIDList(element, probeData, index);
            } catch (error) {
                this.showError(`Failed to probe stream: ${error.message}`);
            }
        });

        // PID input listeners
        element.querySelector('.option-program-number')?.addEventListener('change', (e) => 
            this.handleDemuxChange(index, 'program-number', e.target.value));
        element.querySelector('.option-video-pid')?.addEventListener('change', (e) => 
            this.handleDemuxChange(index, 'video-pid', e.target.value));
        element.querySelector('.option-audio-pid')?.addEventListener('change', (e) => 
            this.handleDemuxChange(index, 'audio-pid', e.target.value));

        // Type-specific listeners
        const input = this.currentInputs[index];
        if (input.type === 'srtsrc') {
            this.attachSRTListeners(element, index);
        } else if (input.type === 'udpsrc') {
            this.attachUDPListeners(element, index);
        } else if (input.type === 'hlssrc') {
            this.attachHLSListeners(element, index);
        } else if (input.type === 'rtspsrc') {
            this.attachRTSPListeners(element, index);
        }
    }

    attachSRTListeners(element, index) {
        element.querySelector('.option-latency')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'latency', parseInt(e.target.value)));
        element.querySelector('.option-mode')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'mode', e.target.value));
        element.querySelector('.option-streamid')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'streamid', e.target.value));
        element.querySelector('.option-timeout')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'poll-timeout', parseInt(e.target.value)));
        element.querySelector('.option-passphrase')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'passphrase', e.target.value));
        element.querySelector('.option-wait-connection')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'wait-for-connection', e.target.checked));
        element.querySelector('.option-auto-reconnect')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'auto-reconnect', e.target.checked));
    }

    attachUDPListeners(element, index) {
        element.querySelector('.option-buffer-size')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'buffer_size', parseInt(e.target.value)));
        element.querySelector('.option-interface')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'interface', e.target.value));
        element.querySelector('.option-do-timestamp')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'do-timestamp', e.target.checked));
    }

    attachHLSListeners(element, index) {
        element.querySelector('.option-timeout')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'timeout', parseInt(e.target.value)));
        element.querySelector('.option-retries')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'retries', parseInt(e.target.value)));
    }

    attachRTSPListeners(element, index) {
        element.querySelector('.option-latency')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'latency', parseInt(e.target.value)));
        element.querySelector('.option-retry-delay')?.addEventListener('change', (e) => 
            this.handleOptionChange(index, 'retry-delay', parseInt(e.target.value)));
    }

updatePIDList(element, probeData, index) {
  const availableContainer = element.querySelector('[data-role="available"]');
  availableContainer.innerHTML = '';

  if (probeData.programs && probeData.programs.length > 0) {
    probeData.programs.forEach(program => {
      const programDiv = document.createElement('div');
      programDiv.className = 'program-item';
      programDiv.innerHTML = `
        <div class="program-header" data-program="${program.program_id}">
          Program ${program.program_id} - ${program.tags.service_name}
        </div>

        <div class="program-pids">
          ${program.streams.map(stream => {
            let streamInfo = `
              <div class="pid-item" 
                   data-pid="${stream.id}" 
                   data-type="${stream.codec_type}"
                   data-program="${program.program_id}">
                ${stream.codec_type.toUpperCase()} - PID: ${stream.id}
                (${stream.codec_name})`;

            if (stream.codec_type === 'audio') {
              streamInfo += `
                (${stream.tags.language})
              `;
            }

            streamInfo += `
              </div>
            `;

            return streamInfo;
          }).join('')}
        </div>
      `;
                availableContainer.appendChild(programDiv);
            });
        } else {
            availableContainer.innerHTML = '<div class="no-pids">No programs or PIDs found</div>';
        }

        // Attach click handlers
        availableContainer.querySelectorAll('.pid-item').forEach(pidItem => {
            pidItem.addEventListener('click', () => this.handlePIDSelection(pidItem, index));
        });
    }

    handlePIDSelection(pidItem, inputIndex) {
        const type = pidItem.dataset.type;
        const pid = pidItem.dataset.pid;
        const programId = pidItem.dataset.program;
        const input = this.currentInputs[inputIndex];

        if (!input.demux) {
            input.demux = {};
        }

        // Update the appropriate PID
        if (type === 'video') {
            input.demux['video-pid'] = pid;
        } else if (type === 'audio') {
            input.demux['audio-pid'] = pid;
        }
        input.demux['program-number'] = programId;

        // Update the selected PIDs display
        const container = pidItem.closest('.input-container');
        const selectedContainer = container.querySelector('[data-role="selected"]');
        
        selectedContainer.querySelector('[data-type="video"]').textContent = 
            input.demux['video-pid'] || 'None';
        selectedContainer.querySelector('[data-type="audio"]').textContent = 
            input.demux['audio-pid'] || 'None';
        selectedContainer.querySelector('[data-type="program"]').textContent = 
            input.demux['program-number'] || 'None';

        this.setUnsavedChanges(true);
    }

    handleDemuxChange(index, field, value) {
        if (!this.currentInputs[index].demux) {
            this.currentInputs[index].demux = {};
        }
        this.currentInputs[index].demux[field] = value;
        this.setUnsavedChanges(true);
    }

    toggleDetails(index) {
        const details = document.getElementById(`input-details-${index}`);
        if (details) {
            details.classList.toggle('hidden');
        }
    }

    addNewInput(type, options = {}) {
        this.currentInputs.push({
            type: type,
            uri: '',
            options: options,
            demux: {}
        });
        this.setUnsavedChanges(true);
        this.renderInputs();
    }

    removeInput(index) {
        this.currentInputs.splice(index, 1);
        this.setUnsavedChanges(true);
        this.renderInputs();
    }

    handleInputChange(index, field, value) {
        this.currentInputs[index][field] = value;
        this.setUnsavedChanges(true);
    }

    handleOptionChange(index, field, value) {
        if (!this.currentInputs[index].options) {
            this.currentInputs[index].options = {};
        }
        this.currentInputs[index].options[field] = value;
        this.setUnsavedChanges(true);
    }

    setUnsavedChanges(value) {
        this.hasUnsavedChanges = value;
        const saveButton = document.getElementById('save-changes-btn');
        if (saveButton) {
            saveButton.classList.toggle('hidden', !value);
        }
    }

    async loadInputConfig() {
        try {
            if (!this.channelName || !this.serverAddress) {
                throw new Error('Missing channel name or server address');
            }

            const response = await fetch(`http://${this.serverAddress}:5000/api/channels`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            const config = data.channels.find(c => c.name === this.channelName);
            
            if (config?.inputs) {
                this.currentInputs = config.inputs;
                this.renderInputs();
            }
        } catch (error) {
            console.error('Error loading input config:', error);
            this.showError(`Failed to load inputs: ${error.message}`);
        }
    }

    renderInputs() {
        const container = document.getElementById('srt-inputs-container');
        if (!container) {
            console.error('Inputs container not found');
            return;
        }

        container.innerHTML = '';
        
        if (this.currentInputs.length === 0) {
            container.innerHTML = `
                <div class="no-inputs-message">
                    No inputs configured. Click "Add Input" to create one.
                </div>
            `;
            return;
        }

        this.currentInputs.forEach((input, index) => {
            const inputElement = this.createInputElement(input, index);
            container.appendChild(inputElement);
        });
    }

    createInputElement(input, index) {
        const div = document.createElement('div');
        div.className = 'input-container';
        div.setAttribute('data-index', index);
        
        div.innerHTML = `
            <div class="input-row">
                <div class="input-header">
                    <input type="text" class="input-uri" 
                           value="${input.uri || ''}" 
                           placeholder="${this.getUriPlaceholder(input.type)}"
                           data-index="${index}">
                    <button class="btn probe-btn" data-index="${index}">Probe</button>
                    <button class="btn toggle-btn" data-index="${index}">Setup</button>
                    <button class="btn delete-btn" data-index="${index}">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M3 6h18"></path>
                            <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path>
                            <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path>
                        </svg>
                    </button>
                </div>
                <div class="pid-selector">
                    <div class="pid-lists">
                        <div class="pid-list available">
                            <h4>Available Programs and PIDs</h4>
                            <div class="pids-container" data-role="available"></div>
                        </div>
                        <div class="pid-list selected">
                            <h4>Selected PIDs</h4>
                            <div class="pids-container" data-role="selected">
                                <div class="pid-group">
                                    <label>Program Number:</label>
                                    <div class="selected-pid" data-type="program">${input.demux?.['program-number'] || 'None'}</div>
                                </div>
                                <div class="pid-group">
                                    <label>Video PID:</label>
                                    <div class="selected-pid" data-type="video">${input.demux?.['video-pid'] || 'None'}</div>
                                </div>
                                <div class="pid-group">
                                    <label>Audio PID:</label>
                                    <div class="selected-pid" data-type="audio">${input.demux?.['audio-pid'] || 'None'}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="input-details hidden" id="input-details-${index}">
                    ${this.renderInputOptions(input, index)}
                </div>
            </div>
        `;

        this.attachInputEventListeners(div, index);
        return div;
    }

    getUriPlaceholder(type) {
        switch (type) {
            case 'srtsrc':
                return 'srt://hostname:port';
            case 'udpsrc':
                return 'udp://239.0.0.0:1234';
            case 'hlssrc':
                return 'http://example.com/stream.m3u8';
            case 'rtspsrc':
                return 'rtsp://hostname:port/path';
            default:
                return 'Enter URI';
        }
    }

    handleInputTypeSelection(type) {
        const defaultOptions = this.getDefaultOptionsForType(type);
        this.addNewInput(type, defaultOptions);
    }

    getDefaultOptionsForType(type) {
        switch (type) {
            case 'srtsrc':
                return {
                    latency: 1000,
                    mode: 'caller',
                    'poll-timeout': 1000,
                    'wait-for-connection': true,
                    'auto-reconnect': true
                };
            case 'udpsrc':
                return {
                    buffer_size: 2097152,
                    'do-timestamp': true
                };
            case 'hlssrc':
                return {
                    timeout: 10,
                    retries: 3
                };
            case 'rtspsrc':
                return {
                    latency: 2000,
                    'retry-delay': 5,
                    'retry-attempts': 3
                };
            default:
                return {};
        }
    }

    async saveChanges() {
        try {
            if (!this.channelName || !this.serverAddress) {
                throw new Error('Missing channel name or server address');
            }

            const saveButton = document.getElementById('save-changes-btn');
            if (saveButton) {
                saveButton.textContent = 'Saving...';
                saveButton.disabled = true;
            }

            const response = await fetch(`http://${this.serverAddress}:8001/config/update`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    channel: this.channelName,
                    config: { inputs: this.currentInputs }
                })
            });

            if (!response.ok) {
                throw new Error(`Failed to save configuration: ${response.statusText}`);
            }

            const restartResponse = await fetch(`http://${this.serverAddress}:8001/restart`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel: this.channelName })
            });

            if (!restartResponse.ok) {
                throw new Error('Failed to restart channel');
            }

            this.setUnsavedChanges(false);
            this.showSuccess('Changes saved successfully');

        } catch (error) {
            console.error('Error saving changes:', error);
            this.showError(`Failed to save changes: ${error.message}`);
        } finally {
            const saveButton = document.getElementById('save-changes-btn');
            if (saveButton) {
                saveButton.textContent = 'Save Changes';
                saveButton.disabled = false;
            }
        }
    }

    showError(message) {
        const container = document.getElementById('srt-inputs-container');
        if (container) {
            const errorDiv = document.createElement('div');
            errorDiv.className = 'error-message';
            errorDiv.textContent = message;
            container.insertBefore(errorDiv, container.firstChild);
            setTimeout(() => errorDiv.remove(), 5000);
        }
    }

    showSuccess(message) {
        const container = document.getElementById('srt-inputs-container');
        if (container) {
            const successDiv = document.createElement('div');
            successDiv.className = 'success-message';
            successDiv.textContent = message;
            container.insertBefore(successDiv, container.firstChild);
            setTimeout(() => successDiv.remove(), 3000);
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const inputConfig = new InputConfig();
    window.inputConfigInstance = inputConfig;
    inputConfig.init();
});

// Confirm before leaving if there are unsaved changes
window.addEventListener('beforeunload', (e) => {
    const inputConfig = window.inputConfigInstance;
    if (inputConfig?.hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
    }
});

export default InputConfig;