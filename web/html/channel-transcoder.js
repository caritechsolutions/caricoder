// channel-transcoder.js
import { channelState, STATE_EVENTS } from './modules/state.js';
import { fetchServerList } from './modules/api.js';
import { x264Settings } from './modules/transcoder/x264enc.js';
import { x265Settings } from './modules/transcoder/x265enc.js';
import { aacSettings } from './modules/transcoder/avenc_aac.js';

class TranscoderConfig {
    constructor() {
        this.hasUnsavedChanges = false;
        this.serverAddress = localStorage.getItem('selectedServer') || '192.168.110.42';
        this.channelName = new URLSearchParams(window.location.search).get('name');
        this.currentConfig = null;
    }



    // In TranscoderConfig class
async loadConfig() {
    console.log('Loading transcoder configuration...');
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
        
        if (config?.transcoding) {
            this.currentConfig = config;
            this.updateUI(config.transcoding);
        }
    } catch (error) {
        console.error('Error loading transcoder config:', error);
        this.showError(`Failed to load configuration: ${error.message}`);
    }
}

updateUI(transcoding) {
    console.log('Updating UI with transcoding config:', transcoding);

    // Video Configuration
    const videoCodecSelect = document.getElementById('video-codec');
    if (videoCodecSelect) {
        // Handle both single codec and streams array format
        let videoSettings;
        if (Array.isArray(transcoding.video?.streams)) {
            videoSettings = transcoding.video.streams[0];  // Use first stream settings
        } else {
            videoSettings = transcoding.video;
        }

        const codec = videoSettings?.codec || 'passthrough';
        videoCodecSelect.value = this.mapConfigCodecToUI(codec);
        
        // Trigger codec change handler to show options
        this.handleVideoCodecChange(videoCodecSelect.value);

        // After options are created, populate their values
        if (codec !== 'passthrough') {
            this.populateVideoEncoderSettings(videoSettings);
        }
    }

    // Audio Configuration
    const audioCodecSelect = document.getElementById('audio-codec');
    if (audioCodecSelect) {
        const audioSettings = transcoding.audio;
        const codec = audioSettings?.codec || 'passthrough';
        audioCodecSelect.value = codec;
        
        this.handleAudioCodecChange(codec);
        
        if (codec !== 'passthrough') {
            this.populateAudioEncoderSettings(audioSettings);
        }
    }
}



mapConfigCodecToUI(codec) {
    // Map config file codec names to UI select values
    const codecMap = {
        'x264': 'x264enc',
        'nvenc_h264': 'nvh264enc',
        'x265': 'x265enc',
        'nvenc_h265': 'nvh265enc',
        'mpeg2': 'mpeg2enc',
        'passthrough': 'passthrough'
    };
    return codecMap[codec] || codec;
}


populateVideoEncoderSettings(settings) {
    // Wait brief moment for options to be created
    setTimeout(() => {
        const videoOptions = document.getElementById('video-options');
        if (!videoOptions) return;

        // Populate x264enc settings
        if (settings.codec === 'x264' || settings.codec === 'x264enc') {
            // Bitrate
            const bitrateInput = videoOptions.querySelector('.option-bitrate');
            if (bitrateInput) bitrateInput.value = settings.options?.bitrate || 2048;

            // Speed Preset
            const presetSelect = videoOptions.querySelector('.option-speed-preset');
            if (presetSelect) presetSelect.value = settings.options?.preset || 'medium';

            // Tune Options (multiple select)
            const tuneSelect = videoOptions.querySelector('.option-tune');
            if (tuneSelect && settings.options?.tune) {
                const tunes = Array.isArray(settings.options.tune) 
                    ? settings.options.tune 
                    : [settings.options.tune];
                    
                Array.from(tuneSelect.options).forEach(option => {
                    option.selected = tunes.includes(option.value);
                });
            }

            // Key Frame Interval
            const keyintInput = videoOptions.querySelector('.option-keyint');
            if (keyintInput) keyintInput.value = settings.options?.['key-int-max'] || 60;

            // B-Frames
            const bframesInput = videoOptions.querySelector('.option-bframes');
            if (bframesInput) bframesInput.value = settings.options?.bframes || 0;

            // Checkboxes
            const checkboxMappings = {
                'b-adapt': 'b-adapt',
                'b-pyramid': 'b-pyramid',
                'weightb': 'weightb',
                'cabac': 'cabac',
                'dct8x8': 'dct8x8',
                'interlaced': 'interlaced',
                'intra-refresh': 'intra-refresh'
            };

            Object.entries(checkboxMappings).forEach(([optionName, configName]) => {
                const checkbox = videoOptions.querySelector(`.option-${optionName}`);
                if (checkbox && settings.options?.[configName] !== undefined) {
                    checkbox.checked = settings.options[configName];
                }
            });

            // Motion Estimation
            const meSelect = videoOptions.querySelector('.option-me');
            if (meSelect) meSelect.value = settings.options?.me || 'hex';

            // Subpixel ME Quality
            const submeInput = videoOptions.querySelector('.option-subme');
            if (submeInput) submeInput.value = settings.options?.subme || 1;

            // Noise Reduction
            const nrInput = videoOptions.querySelector('.option-noise-reduction');
            if (nrInput) nrInput.value = settings.options?.['noise-reduction'] || 0;

            // QP settings
            const qpSettings = {
                'qp-min': videoOptions.querySelector('.option-qp-min'),
                'qp-max': videoOptions.querySelector('.option-qp-max'),
                'qp-step': videoOptions.querySelector('.option-qp-step')
            };

            Object.entries(qpSettings).forEach(([name, element]) => {
                if (element && settings.options?.[name] !== undefined) {
                    element.value = settings.options[name];
                }
            });
        }

        // Similar blocks for other video encoders (x265, nvenc, etc.)
        // Add conditions and settings for other encoders as needed
    }, 100);  // Small delay to ensure options are rendered
}

populateAudioEncoderSettings(settings) {
    setTimeout(() => {
        const audioOptions = document.getElementById('audio-options');
        if (!audioOptions) return;

        if (settings.codec === 'avenc_aac') {
            // Populate AAC-specific settings
            const bitrate = audioOptions.querySelector('.option-bitrate');
            if (bitrate) bitrate.value = settings.options?.bitrate || 128;

            // Add other AAC-specific settings
        } else if (settings.codec === 'avenc_ac3') {
            // Populate AC3-specific settings
        } else if (settings.codec === 'avenc_mp2') {
            // Populate MP2-specific settings
        }
    }, 100);
}

async loadConfig() {
    console.log('Loading transcoder configuration...');
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
        
        if (config?.transcoding) {
            this.currentConfig = config;
            this.updateUI(config.transcoding);
            console.log('Configuration loaded and UI updated');
        } else {
            console.warn('No transcoding configuration found');
        }
    } catch (error) {
        console.error('Error loading transcoder config:', error);
        this.showError(`Failed to load configuration: ${error.message}`);
    }
}


showError(message) {
    // We can implement error display UI later
    console.error(message);
}

    async init() {
        console.log('Transcoder Config Initializing...');
        
        // Initialize server select
        await this.initializeServerSelect();
        
        // Add event listeners
        this.attachEventListeners();

        // Load initial config
        await this.loadConfig();

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
                this.loadConfig();
            });
        } catch (error) {
            console.error('Error loading server list:', error);
        }
    }

    attachEventListeners() {
        // Video codec change handler
        document.getElementById('video-codec')?.addEventListener('change', (e) => {
            this.handleVideoCodecChange(e.target.value);
        });

        // Audio codec change handler
        document.getElementById('audio-codec')?.addEventListener('change', (e) => {
            this.handleAudioCodecChange(e.target.value);
        });

        // Save button
        document.getElementById('save-changes-btn')?.addEventListener('click', () => {
            this.saveChanges();
        });
    }

    handleVideoCodecChange(codec) {
        const optionsContainer = document.getElementById('video-options');
        optionsContainer.innerHTML = '';

        if (codec === 'passthrough') {
            optionsContainer.classList.add('hidden');
            return;
        }

        let options;
        switch (codec) {
            case 'x264enc':
                options = x264Settings.getOptionsHTML(this.currentConfig);
                break;
            case 'x265enc':
                options = x265Settings.getOptionsHTML(this.currentConfig);
                break;
            // Add other codec cases here
        }

        if (options) {
            optionsContainer.innerHTML = options;
            optionsContainer.classList.remove('hidden');
            this.attachOptionsEventListeners(codec);
        }
    }

    handleAudioCodecChange(codec) {
    const optionsContainer = document.getElementById('audio-options');
    optionsContainer.innerHTML = '';

    if (codec === 'passthrough') {
        optionsContainer.classList.add('hidden');
        return;
    }

    let options;
    switch (codec) {
        case 'avenc_aac':
            options = aacSettings.getOptionsHTML(this.currentConfig);
            break;
        // Add cases for other audio codecs
    }

    if (options) {
        optionsContainer.innerHTML = options;
        optionsContainer.classList.remove('hidden');
        this.attachAudioOptionsEventListeners(codec);
    }
}

attachAudioOptionsEventListeners(codec) {
    switch (codec) {
        case 'avenc_aac':
            aacSettings.attachEventListeners(this);
            break;
        // Add cases for other audio codecs
    }
}

// Update populateAudioEncoderSettings
populateAudioEncoderSettings(settings) {
    setTimeout(() => {
        if (settings.codec === 'avenc_aac') {
            this.handleAudioCodecChange('avenc_aac');  // This will create the elements
            // Additional settings will be populated by getOptionsHTML using the current config
        }
    }, 100);
}


    attachOptionsEventListeners(codec) {
        switch (codec) {
            case 'x264enc':
                x264Settings.attachEventListeners(this);
                break;
            case 'x265enc':
                x265Settings.attachEventListeners(this);
                break;
            // Add other codec cases here
        }
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

    // Rest of the class implementation...
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const transcoderConfig = new TranscoderConfig();
    window.transcoderConfigInstance = transcoderConfig;
    transcoderConfig.init();
});

// Confirm before leaving if there are unsaved changes
window.addEventListener('beforeunload', (e) => {
    const transcoderConfig = window.transcoderConfigInstance;
    if (transcoderConfig?.hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
    }
});