// modules/transcoder/x265enc.js
export const x265Settings = {
    getOptionsHTML(config) {
        const currentSettings = config?.transcoding?.video || {};
        return `
            <div class="option-group">
                <label>Bitrate (kbit/s):</label>
                <input type="number" 
                       class="option-bitrate"
                       value="${currentSettings.bitrate || 2048}"
                       min="100"
                       max="50000">
            </div>
            <div class="option-group">
                <label>Speed Preset:</label>
                <select class="option-speed-preset">
                    <option value="ultrafast" ${currentSettings.preset === 'ultrafast' ? 'selected' : ''}>Ultra Fast</option>
                    <option value="superfast" ${currentSettings.preset === 'superfast' ? 'selected' : ''}>Super Fast</option>
                    <option value="veryfast" ${currentSettings.preset === 'veryfast' ? 'selected' : ''}>Very Fast</option>
                    <option value="faster" ${currentSettings.preset === 'faster' ? 'selected' : ''}>Faster</option>
                    <option value="fast" ${currentSettings.preset === 'fast' ? 'selected' : ''}>Fast</option>
                    <option value="medium" ${currentSettings.preset === 'medium' ? 'selected' : ''}>Medium</option>
                    <option value="slow" ${currentSettings.preset === 'slow' ? 'selected' : ''}>Slow</option>
                    <option value="slower" ${currentSettings.preset === 'slower' ? 'selected' : ''}>Slower</option>
                    <option value="veryslow" ${currentSettings.preset === 'veryslow' ? 'selected' : ''}>Very Slow</option>
                </select>
            </div>
            <div class="option-group">
                <label>Profile:</label>
                <select class="option-profile">
                    <option value="main" ${currentSettings.profile === 'main' ? 'selected' : ''}>Main</option>
                    <option value="main10" ${currentSettings.profile === 'main10' ? 'selected' : ''}>Main 10</option>
                    <option value="main444-8" ${currentSettings.profile === 'main444-8' ? 'selected' : ''}>Main 444 8-bit</option>
                    <option value="main444-10" ${currentSettings.profile === 'main444-10' ? 'selected' : ''}>Main 444 10-bit</option>
                </select>
            </div>
            <div class="option-group">
                <label>Key Frame Interval:</label>
                <input type="number" 
                       class="option-keyint"
                       value="${currentSettings.keyint || 60}"
                       min="1"
                       max="300">
            </div>
        `;
    },

    attachEventListeners(context) {
        document.querySelector('.option-bitrate')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
            // Add specific handling
        });

        document.querySelector('.option-speed-preset')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
            // Add specific handling
        });

        document.querySelector('.option-profile')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
            // Add specific handling
        });

        document.querySelector('.option-keyint')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
            // Add specific handling
        });
    }
};
