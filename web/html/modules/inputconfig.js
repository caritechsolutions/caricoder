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
        // Create dialog element
        const dialog = document.createElement('dialog');
        dialog.className = 'input-type-dialog';
        
        dialog.innerHTML = `
            <div class="dialog-content">
                <h3>Select Input Type</h3>
                <div class="input-type-grid">
                    <button class="input-type-option" data-type="srtsrc">
                        <div class="option-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                                <path d="M12 2.2V12l4.5-4.5"/>
                            </svg>
                        </div>
                        <span class="option-label">SRT Input</span>
                        <span class="option-description">Secure Reliable Transport protocol for low-latency streaming</span>
                    </button>

                    <button class="input-type-option" data-type="udpsrc">
                        <div class="option-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
                                <path d="m6 12 4-4v8"/>
                                <path d="M14 9v6"/>
                                <path d="M18 9v6"/>
                            </svg>
                        </div>
                        <span class="option-label">UDP Input</span>
                        <span class="option-description">User Datagram Protocol for fast, connectionless streaming</span>
                    </button>

                    <button class="input-type-option" data-type="hlssrc">
                        <div class="option-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0-3-3H6a3 3 0 0 0-3 3 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3 3 3 0 0 0 3 3h12a3 3 0 0 0 3-3 3 3 0 0 0-3-3z"/>
                            </svg>
                        </div>
                        <span class="option-label">HLS Input</span>
                        <span class="option-description">HTTP Live Streaming for adaptive bitrate streaming</span>
                    </button>

                    <button class="input-type-option" data-type="rtspsrc">
                        <div class="option-icon">
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/>
                                <path d="M12 7m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/>
                                <path d="M12 17m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/>
                                <path d="M17 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/>
                                <path d="M7 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0"/>
                            </svg>
                        </div>
                        <span class="option-label">RTSP Input</span>
                        <span class="option-description">Real Time Streaming Protocol for IP cameras and streams</span>
                    </button>
                </div>
                <div class="dialog-actions">
                    <button class="btn btn-secondary close-dialog">Cancel</button>
                </div>
            </div>
        `;

        // Add event listeners
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

        // Close on click outside
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



    // Add this new method
    handleInputTypeSelection(type) {
        console.log(`Selected input type: ${type}`);
        const defaultOptions = this.getDefaultOptionsForType(type);
        this.addNewInput(type, defaultOptions);
    }

    async init() {
        console.log('Input Config Initializing...');
        
        // Initialize server select
        await this.initializeServerSelect();
        
        // Add event listeners
        this.attachEventListeners();

        // Load initial config
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

    // Set channel name in header
    document.getElementById('channel-name').textContent = channelState.name;


       // Update navigation links with channel name
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

    attachEventListeners() {
        document.getElementById('add-input-btn')?.addEventListener('click', () => {
            this.inputTypeSelector.show();
        });

        document.getElementById('save-changes-btn')?.addEventListener('click', () => {
            this.saveChanges();
        });
    }

    async loadInputConfig() {
        console.log('Loading input configuration...');
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
                this.currentInputs = config.inputs.filter(input => input.type === 'srtsrc');
                this.renderInputs();
            }
        } catch (error) {
            console.error('Error loading input config:', error);
            this.showError(`Failed to load inputs: ${error.message}`);
        }
    }

    renderInputs() {
        console.log('Rendering inputs...');
        const container = document.getElementById('srt-inputs-container');
        if (!container) {
            console.error('SRT inputs container not found');
            return;
        }

        // Clear existing content
        container.innerHTML = '';
        
        if (this.currentInputs.length === 0) {
            container.innerHTML = `
                <div class="no-inputs-message">
                    No SRT inputs configured. Click "Add Input" to create one.
                </div>
            `;
            return;
        }

        // Create and append each input element
        this.currentInputs.forEach((input, index) => {
            console.log(`Creating input element ${index}:`, input);
            const inputElement = this.createInputElement(input, index);
            container.appendChild(inputElement);
        });
    }

    createInputElement(input, index) {
        console.log(`Creating element for input ${index}`);
        const div = document.createElement('div');
        div.className = 'input-container';
        div.setAttribute('data-index', index);
        
        div.innerHTML = `
        <div class="input-row">
            <div class="input-header">
                <input type="text" class="input-uri" 
                       value="${input.uri || ''}" 
                       placeholder="srt://hostname:port"
                       data-index="${index}">
                <button class="btn toggle-btn" data-index="${index}">Setup</button>
                <button class="btn delete-btn" data-index="${index}">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M3 6h18"></path>
                        <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path>
                        <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path>
                    </svg>
                </button>
            </div>
        </div>
            <div class="input-details hidden" id="input-details-${index}">
                <div class="options-grid">
                    <div class="option-group">
                        <label>Latency (ms):</label>
                        <input type="number" class="option-latency"
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
                        <input type="text" class="option-streamid"
                               value="${input.options?.streamid || ''}"
                               placeholder="Optional stream identifier"
                               data-index="${index}">
                    </div>
                    <div class="option-group">
                        <label>Poll Timeout (ms):</label>
                        <input type="number" class="option-timeout"
                               value="${input.options?.['poll-timeout'] || 1000}"
                               data-index="${index}">
                    </div>
                    <div class="option-group">
                        <label>Passphrase:</label>
                        <input type="password" class="option-passphrase"
                               value="${input.options?.passphrase || ''}"
                               placeholder="Optional encryption passphrase"
                               data-index="${index}">
                    </div>
                    <div class="option-group">
                        <label>Key Length:</label>
                        <select class="option-keylen" data-index="${index}">
                            <option value="0" ${input.options?.pbkeylen === '0' ? 'selected' : ''}>No Key</option>
                            <option value="16" ${input.options?.pbkeylen === '16' ? 'selected' : ''}>16</option>
                            <option value="24" ${input.options?.pbkeylen === '24' ? 'selected' : ''}>24</option>
                            <option value="32" ${input.options?.pbkeylen === '32' ? 'selected' : ''}>32</option>
                        </select>
                    </div>
                </div>
                <div class="checkbox-options">
                    <label>
                        <input type="checkbox" class="option-wait-connection"
                               ${input.options?.['wait-for-connection'] ? 'checked' : ''}
                               data-index="${index}">
                        Wait for connection
                    </label>
                    <label>
                        <input type="checkbox" class="option-auto-reconnect"
                               ${input.options?.['auto-reconnect'] ? 'checked' : ''}
                               data-index="${index}">
                        Auto reconnect
                    </label>
                </div>
            </div>
        `;

        // Add event listeners
        this.attachInputEventListeners(div, index);
        return div;
    }

    attachInputEventListeners(element, index) {
        element.querySelector('.toggle-btn')?.addEventListener('click', () => this.toggleDetails(index));
        element.querySelector('.delete-btn')?.addEventListener('click', () => this.removeInput(index));
        element.querySelector('.input-uri')?.addEventListener('change', (e) => this.handleInputChange(index, 'uri', e.target.value));
        element.querySelector('.option-latency')?.addEventListener('change', (e) => this.handleOptionChange(index, 'latency', parseInt(e.target.value)));
        element.querySelector('.option-mode')?.addEventListener('change', (e) => this.handleOptionChange(index, 'mode', e.target.value));
        element.querySelector('.option-streamid')?.addEventListener('change', (e) => this.handleOptionChange(index, 'streamid', e.target.value));
        element.querySelector('.option-timeout')?.addEventListener('change', (e) => this.handleOptionChange(index, 'poll-timeout', parseInt(e.target.value)));
        element.querySelector('.option-passphrase')?.addEventListener('change', (e) => this.handleOptionChange(index, 'passphrase', e.target.value));
        element.querySelector('.option-keylen')?.addEventListener('change', (e) => this.handleOptionChange(index, 'pbkeylen', e.target.value));
        element.querySelector('.option-wait-connection')?.addEventListener('change', (e) => this.handleOptionChange(index, 'wait-for-connection', e.target.checked));
        element.querySelector('.option-auto-reconnect')?.addEventListener('change', (e) => this.handleOptionChange(index, 'auto-reconnect', e.target.checked));
    }

    toggleDetails(index) {
    const details = document.getElementById(`input-details-${index}`);
    const button = document.querySelector(`.toggle-btn[data-index="${index}"]`);
    if (details && button) {
        details.classList.toggle('hidden');
            
        }
    }

    addNewInput(type, options = {}) {
        console.log('Adding new input:', type);
        this.currentInputs.push({
            type: type,
            uri: '',
            options: options
        });
        this.setUnsavedChanges(true);
        this.renderInputs();
    }



    // Add this method to get default options per type
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


    removeInput(index) {
        console.log(`Removing input ${index}`);
        this.currentInputs.splice(index, 1);
        this.setUnsavedChanges(true);
        this.renderInputs();
    }

    handleInputChange(index, field, value) {
        console.log(`Handling input change for index ${index}, field ${field}, value ${value}`);
        this.currentInputs[index][field] = value;
        this.setUnsavedChanges(true);
    }

    handleOptionChange(index, field, value) {
        console.log(`Handling option change for index ${index}, field ${field}, value ${value}`);
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
            if (value) {
                saveButton.classList.remove('hidden');
            } else {
                saveButton.classList.add('hidden');
            }
        }
    }

    async saveChanges() {
        try {
            if (!this.channelName || !this.serverAddress) {
                throw new Error('Missing channel name or server address');
            }

            // Show saving indicator
            const saveButton = document.getElementById('save-changes-btn');
            if (saveButton) {
                saveButton.textContent = 'Saving...';
                saveButton.disabled = true;
            }

            console.log('Saving changes:', {
                channel: this.channelName,
                inputs: this.currentInputs
            });

            // Save configuration
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

            // Restart channel
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
            // Reset save button
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

            // Remove error after 5 seconds
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

            // Remove success message after 3 seconds
            setTimeout(() => successDiv.remove(), 3000);
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const inputConfig = new InputConfig();
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
