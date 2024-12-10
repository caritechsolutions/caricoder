// modules/transcoder/avenc_aac.js

export const aacSettings = {
    getOptionsHTML(config) {
        const currentSettings = config?.transcoding?.audio || {};
        const options = currentSettings.options || {};
        
        return `
            <div class="settings-group">
                <h4>Advanced Options</h4>
                <div class="option-checkboxes">
                    <label>
                        <input type="checkbox" 
                               class="option-aac-is"
                               ${options['aac-is'] !== false ? 'checked' : ''}>
                        Intensity Stereo
                    </label>
                    <label>
                        <input type="checkbox" 
                               class="option-aac-tns"
                               ${options['aac-tns'] !== false ? 'checked' : ''}>
                        Temporal Noise Shaping
                    </label>
                    <label>
                        <input type="checkbox" 
                               class="option-aac-pns"
                               ${options['aac-pns'] !== false ? 'checked' : ''}>
                        Perceptual Noise Substitution
                    </label>
                    <label>
                        <input type="checkbox" 
                               class="option-aac-ltp"
                               ${options['aac-ltp'] === true ? 'checked' : ''}>
                        Long Term Prediction
                    </label>
                    <label>
                        <input type="checkbox" 
                               class="option-aac-pred"
                               ${options['aac-pred'] === true ? 'checked' : ''}>
                        AAC-Main Prediction
                    </label>
                    <label>
                        <input type="checkbox" 
                               class="option-aac-pce"
                               ${options['aac-pce'] === true ? 'checked' : ''}>
                        Force PCE Usage
                    </label>
                </div>
                <div class="option-group">
                    <label>M/S Stereo Coding:</label>
                    <select class="option-aac-ms">
                        <option value="-1" ${options['aac-ms'] === -1 ? 'selected' : ''}>Auto</option>
                        <option value="0" ${options['aac-ms'] === 0 ? 'selected' : ''}>Disabled</option>
                        <option value="1" ${options['aac-ms'] === 1 ? 'selected' : ''}>Enabled</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Frame Size:</label>
                    <input type="number"
                           class="option-frame-size"
                           value="${options['frame-size'] || 0}"
                           min="0"
                           max="4096">
                </div>
                <div class="option-group">
                    <label>Compression Level:</label>
                    <input type="number"
                           class="option-compression-level"
                           value="${options['compression-level'] || -1}"
                           min="-1"
                           max="10">
                </div>
            </div>
            <div class="settings-group">
                <h4>Rate Control</h4>
                <div class="option-group">
                    <label>Bitrate (kbit/s):</label>
                    <input type="number" 
                           class="option-bitrate"
                           value="${options.bitrate || 128}"
                           min="8"
                           max="512">
                </div>
                <div class="option-group">
                    <label>Quality (VBR):</label>
                    <input type="number"
                           class="option-quality"
                           value="${options['global-quality'] || 0}"
                           min="0"
                           max="100">
                </div>
            </div>

            <div class="settings-group">
                <h4>Encoding Algorithm</h4>
                <div class="option-group">
                    <label>AAC Coder:</label>
                    <select class="option-aac-coder">
                        <option value="twoloop" ${options['aac-coder'] === 'twoloop' ? 'selected' : ''}>Two Loop (Better Quality)</option>
                        <option value="anmr" ${options['aac-coder'] === 'anmr' ? 'selected' : ''}>ANMR Method</option>
                        <option value="fast" ${options['aac-coder'] === 'fast' ? 'selected' : ''}>Fast Search</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Sample Rate:</label>
                    <select class="option-sample-rate">
                        <option value="96000" ${options.ar === 96000 ? 'selected' : ''}>96000 Hz</option>
                        <option value="88200" ${options.ar === 88200 ? 'selected' : ''}>88200 Hz</option>
                        <option value="48000" ${options.ar === 48000 ? 'selected' : ''}>48000 Hz</option>
                        <option value="44100" ${options.ar === 44100 ? 'selected' : ''}>44100 Hz</option>
                        <option value="32000" ${options.ar === 32000 ? 'selected' : ''}>32000 Hz</option>
                        <option value="24000" ${options.ar === 24000 ? 'selected' : ''}>24000 Hz</option>
                        <option value="22050" ${options.ar === 22050 ? 'selected' : ''}>22050 Hz</option>
                        <option value="16000" ${options.ar === 16000 ? 'selected' : ''}>16000 Hz</option>
                    </select>
                </div>
            </div>

            
        `;
    },

    attachEventListeners(context) {
        // Rate Control
        document.querySelector('.option-bitrate')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-quality')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        // Encoding Algorithm
        document.querySelector('.option-aac-coder')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-sample-rate')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        // Advanced Options - Checkboxes
        const checkboxes = [
            'aac-is', 'aac-tns', 'aac-pns', 'aac-ltp', 
            'aac-pred', 'aac-pce'
        ];

        checkboxes.forEach(ctrl => {
            document.querySelector(`.option-${ctrl}`)?.addEventListener('change', (e) => {
                context.setUnsavedChanges(true);
            });
        });

        // Other Advanced Options
        document.querySelector('.option-aac-ms')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-frame-size')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-compression-level')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });
    },

    // Helper method to get current settings
    getCurrentSettings() {
        const settings = {
            codec: 'avenc_aac',
            options: {}
        };

        // Rate Control
        const bitrate = document.querySelector('.option-bitrate')?.value;
        if (bitrate) settings.options.bitrate = parseInt(bitrate);

        const quality = document.querySelector('.option-quality')?.value;
        if (quality) settings.options['global-quality'] = parseInt(quality);

        // Encoding Algorithm
        const coder = document.querySelector('.option-aac-coder')?.value;
        if (coder) settings.options['aac-coder'] = coder;

        const sampleRate = document.querySelector('.option-sample-rate')?.value;
        if (sampleRate) settings.options.ar = parseInt(sampleRate);

        // Advanced Options - Checkboxes
        const checkboxOptions = {
            'aac-is': true,
            'aac-tns': true,
            'aac-pns': true,
            'aac-ltp': false,
            'aac-pred': false,
            'aac-pce': false
        };

        Object.keys(checkboxOptions).forEach(option => {
            const checkbox = document.querySelector(`.option-${option}`);
            if (checkbox) {
                settings.options[option] = checkbox.checked;
            }
        });

        // Other Advanced Options
        const msMode = document.querySelector('.option-aac-ms')?.value;
        if (msMode) settings.options['aac-ms'] = parseInt(msMode);

        const frameSize = document.querySelector('.option-frame-size')?.value;
        if (frameSize) settings.options['frame-size'] = parseInt(frameSize);

        const compressionLevel = document.querySelector('.option-compression-level')?.value;
        if (compressionLevel) settings.options['compression-level'] = parseInt(compressionLevel);

        return settings;
    }
};