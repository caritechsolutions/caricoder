import { channelState, STATE_EVENTS } from './modules/state.js';

class ChannelWizard {
    constructor() {
        this.currentStep = 1;
        this.maxSteps = 4;
        this.isSubmitting = false;
        this.init();
    }

    async init() {
        try {
            const serverIP = await this.getServerIP();
            channelState.setServer(serverIP);

            this.modal = document.getElementById('channel-wizard');
            this.nextBtn = document.getElementById('next-btn');
            this.prevBtn = document.getElementById('prev-btn');
            this.addChannelBtn = document.getElementById('add-channel-btn');
            this.closeBtn = this.modal.querySelector('.close-btn');
            
            this.formData = {
                name: '',
                input: { type: 'srtsrc', uri: '' },
                transcoding: {
                    video: { codec: 'passthrough' },
                    audio: { codec: 'passthrough' }
                },
                outputs: [{ type: 'udpsink', host: '', port: '' }]
            };
            
            this.setupEventListeners();
        } catch (error) {
            console.error('Error initializing wizard:', error);
        }
    }

    async getServerIP() {
        try {
            const serverFromUrl = this.getUrlParameter('server');
            if (serverFromUrl) {
                return serverFromUrl;
            }
            const serverIPs = await this.fetchServerIPs();
            return serverIPs.length > 0 ? serverIPs[0] : null;
        } catch (error) {
            console.error('Error getting server IP:', error);
            return null;
        }
    }

    async fetchServerIPs() {
        try {
            const response = await fetch('servers.json');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            return Array.isArray(data.servers) ? data.servers : [];
        } catch (error) {
            console.error('Error fetching server IPs:', error);
            return [];
        }
    }

    getUrlParameter(name) {
        try {
            name = name.replace(/[\[]/, '\\[').replace(/[\]]/, '\\]');
            var regex = new RegExp('[\\?&]' + name + '=([^&#]*)');
            var results = regex.exec(location.search);
            return results === null ? '' : decodeURIComponent(results[1].replace(/\+/g, ' '));
        } catch (error) {
            console.error('Error getting URL parameter:', error);
            return '';
        }
    }

    setupEventListeners() {
        this.addChannelBtn.addEventListener('click', () => this.open());
        this.closeBtn.addEventListener('click', () => this.close());
        this.nextBtn.addEventListener('click', () => this.nextStep());
        this.prevBtn.addEventListener('click', () => this.previousStep());
        this.modal.addEventListener('click', (e) => {
            if (e.target === this.modal) this.close();
        });
    }

    open() {
        this.modal.classList.remove('hidden');
        this.currentStep = 1;
        this.updateStepDisplay();
        this.resetForm();
    }

    close() {
        this.modal.classList.add('hidden');
        this.resetForm();
    }

    resetForm() {
       this.isSubmitting = false;
    this.nextBtn.disabled = false;
    
    // Only reset elements within the modal
    this.modal.querySelectorAll('input').forEach(input => input.value = '');
    this.modal.querySelectorAll('select').forEach(select => select.selectedIndex = 0);
    
    this.formData = {
        name: '',
        input: { type: 'srtsrc', uri: '' },
        transcoding: {
            video: { codec: 'passthrough' },
            audio: { codec: 'passthrough' }
        },
        outputs: [{ type: 'udpsink', host: '', port: '' }]
    };
    }

    updateStepDisplay() {
        document.querySelectorAll('.step').forEach(step => {
            step.classList.toggle('active', parseInt(step.dataset.step) === this.currentStep);
        });

        document.querySelectorAll('.step-content').forEach(content => {
            content.classList.toggle('active', parseInt(content.dataset.step) === this.currentStep);
        });

        this.prevBtn.disabled = this.currentStep === 1;
        this.nextBtn.textContent = this.currentStep === this.maxSteps ? 'Create' : 'Next';
    }

    nextStep() {
        if (!this.validateCurrentStep()) {
            return;
        }

        if (this.currentStep === this.maxSteps) {
            if (!this.isSubmitting) {
                this.createChannel();
            }
        } else {
            this.currentStep++;
            this.updateStepDisplay();
        }
    }

    previousStep() {
        if (this.currentStep > 1) {
            this.currentStep--;
            this.updateStepDisplay();
        }
    }

    validateCurrentStep() {
        let isValid = true;
        let errorMessage = '';

        switch(this.currentStep) {
            case 1:
                const name = document.getElementById('channel-name-input').value.trim();
                if (!name) {
                    errorMessage = 'Please enter a channel name';
                    isValid = false;
                }
                this.formData.name = name;
                break;

            case 2:
                const uri = document.getElementById('input-uri').value.trim();
                if (!uri) {
                    errorMessage = 'Please enter an input URI';
                    isValid = false;
                }
                this.formData.input.type = document.getElementById('input-type').value;
                this.formData.input.uri = uri;
                break;

            case 3:
                const videoCodec = document.getElementById('video-codec').value;
                const audioCodec = document.getElementById('audio-codec').value;
                this.formData.transcoding.video.codec = videoCodec;
                this.formData.transcoding.audio.codec = audioCodec;
                break;

            case 4:
                const outputType = document.getElementById('output-type').value;
                const host = document.getElementById('output-host').value.trim();
                const port = document.getElementById('output-port').value.trim();
                
                if (outputType === 'udpsink') {
                    if (!host || !port) {
                        errorMessage = 'Please enter output host and port';
                        isValid = false;
                    }
                    this.formData.outputs = [{
                        type: outputType,
                        host: host,
                        port: parseInt(port)
                    }];
                }
                break;
        }

        if (!isValid) {
            alert(errorMessage);
        }
        return isValid;
    }

    async createChannel() {
    if (this.isSubmitting) return;

    try {
        this.isSubmitting = true;
        this.nextBtn.disabled = true;

        const configResponse = await fetch(`http://${channelState.server}:5000/api/channels`);
        if (!configResponse.ok) {
            throw new Error(`Failed to fetch current config: ${configResponse.status}`);
        }
        
        const currentConfig = await configResponse.json();
        if (currentConfig.channels.find(c => c.name === this.formData.name)) {
            throw new Error('Channel name already exists');
        }

        let inputOptions = {};
        switch(this.formData.input.type) {
            case 'hlssrc':
                inputOptions = { timeout: 10, retries: 3 };
                break;
            case 'srtsrc':
                inputOptions = { latency: 1000, 'poll-timeout': 1000 };
                break;
            case 'udpsrc':
                inputOptions = { 'buffer-size': 2097152, 'do-timestamp': true };
                break;
        }

        let videoConfig;
        if (this.formData.transcoding.video.codec === 'passthrough') {
            videoConfig = {
                deinterlace: true,
                streams: [{ codec: 'passthrough' }]
            };
        } else {
            videoConfig = {
                deinterlace: true,
                streams: [{
                    codec: this.formData.transcoding.video.codec,
                    resolution: {
                        width: 1920,
                        height: 1080
                    },
                    options: {
                        bitrate: 4000,
                        tune: 'zerolatency',
                        'key-int-max': 60
                    }
                }]
            };
        }

        const channelConfig = {
            inputs: [{
                type: this.formData.input.type,
                uri: this.formData.input.uri,
                options: inputOptions
            }],
            outputs: this.formData.outputs,
            transcoding: {
                video: videoConfig,
                audio: {
                    codec: this.formData.transcoding.audio.codec
                }
            },
            mux: {
                type: 'mpegtsmux',
                bitrate: 3000,
                'program-number': 1000,
                'video-pid': '100',
                'audio-pid': '101'
            }
        };

        const response = await fetch(`http://${channelState.server}:8001/config/add-channel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: this.formData.name,
                config: channelConfig
            })
        });

        if (!response.ok) {
            throw new Error(`Failed to create channel: ${response.status}`);
        }

        alert('Channel created successfully');
        this.close();

    } catch (error) {
        console.error('Error creating channel:', error);
        alert('Error creating channel: ' + error.message);
    } finally {
        this.isSubmitting = false;
        this.nextBtn.disabled = false;
    }
}
}

export default ChannelWizard;