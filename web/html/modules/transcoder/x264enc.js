// modules/transcoder/x264enc.js

// Helper function to create an option string based on the config
const createOptionString = (value) => {
    if (value === undefined || value === '') return '';
    return `value="${value}"`;
};

// Helper function to create a selected string for select options
const createSelectedString = (currentValue, optionValue) => {
    return currentValue === optionValue ? 'selected' : '';
};

export const x264Settings = {
    getOptionsHTML(config) {
        const currentSettings = config?.transcoding?.video || {};
        const videoSettings = Array.isArray(currentSettings.streams) ? 
            currentSettings.streams[0] : currentSettings;

        return `
   
             <!-- Rate Control -->
            <div class="settings-group">
                <h4>Rate Control</h4>
                <div class="option-group">
                    <label>Pass:</label>
                    <select class="option-pass">
                        <option value="cbr" ${createSelectedString(currentSettings.pass, 'cbr')}>Constant Bitrate</option>
                        <option value="quant" ${createSelectedString(currentSettings.pass, 'quant')}>Constant Quantizer</option>
                        <option value="qual" ${createSelectedString(currentSettings.pass, 'qual')}>Constant Quality</option>
                        <option value="pass1" ${createSelectedString(currentSettings.pass, 'pass1')}>VBR - Pass 1</option>
                        <option value="pass2" ${createSelectedString(currentSettings.pass, 'pass2')}>VBR - Pass 2</option>
                        <option value="pass3" ${createSelectedString(currentSettings.pass, 'pass3')}>VBR - Pass 3</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Bitrate (kbit/s):</label>
                    <input type="number" 
                           class="option-bitrate"
                           ${createOptionString(currentSettings.bitrate || 2048)}
                           min="100"
                           max="50000">
                </div>
                <div class="option-group">
                    <label>VBV Buffer Size (ms):</label>
                    <input type="number"
                           class="option-vbv-buf-capacity"
                           ${createOptionString(currentSettings.vbvBufCapacity || 600)}
                           min="0"
                           max="10000">
                </div>
                <div class="option-group">
                    <label>Quantizer Range:</label>
                    <div class="sub-options">
                        <label>Min:</label>
                        <input type="number"
                               class="option-qp-min"
                               ${createOptionString(currentSettings.qpMin || 10)}
                               min="0"
                               max="51">
                        <label>Max:</label>
                        <input type="number"
                               class="option-qp-max"
                               ${createOptionString(currentSettings.qpMax || 51)}
                               min="0"
                               max="51">
                        <label>Step:</label>
                        <input type="number"
                               class="option-qp-step"
                               ${createOptionString(currentSettings.qpStep || 4)}
                               min="1"
                               max="50">
                    </div>
                </div>
            </div>

            <!-- Encoding Presets -->
            <div class="settings-group">
                <h4>Encoding Presets</h4>
                <div class="option-group">
                    <label>Speed Preset:</label>
                    <select class="option-speed-preset">
                        <option value="ultrafast" ${videoSettings.options?.preset === 'ultrafast' ? 'selected' : ''}>Ultra Fast</option>
                        <option value="superfast" ${videoSettings.options?.preset === 'superfast' ? 'selected' : ''}>Super Fast</option>
                        <option value="veryfast" ${videoSettings.options?.preset === 'veryfast' ? 'selected' : ''}>Very Fast</option>
                        <option value="faster" ${videoSettings.options?.preset === 'faster' ? 'selected' : ''}>Faster</option>
                        <option value="fast" ${videoSettings.options?.preset === 'fast' ? 'selected' : ''}>Fast</option>
                        <option value="medium" ${videoSettings.options?.preset === 'medium' ? 'selected' : ''}>Medium</option>
                        <option value="slow" ${videoSettings.options?.preset === 'slow' ? 'selected' : ''}>Slow</option>
                        <option value="slower" ${videoSettings.options?.preset === 'slower' ? 'selected' : ''}>Slower</option>
                        <option value="veryslow" ${videoSettings.options?.preset === 'veryslow' ? 'selected' : ''}>Very Slow</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Tune:</label>
                    <select class="option-tune">
                        <option value="film" ${videoSettings.options?.tune?.includes('film') ? 'selected' : ''}>Film</option>
                        <option value="animation" ${videoSettings.options?.tune?.includes('animation') ? 'selected' : ''}>Animation</option>
                        <option value="grain" ${videoSettings.options?.tune?.includes('grain') ? 'selected' : ''}>Grain</option>
                        <option value="stillimage" ${videoSettings.options?.tune?.includes('stillimage') ? 'selected' : ''}>Still Image</option>
                        <option value="fastdecode" ${videoSettings.options?.tune?.includes('fastdecode') ? 'selected' : ''}>Fast Decode</option>
                        <option value="zerolatency" ${videoSettings.options?.tune?.includes('zerolatency') ? 'selected' : ''}>Zero Latency</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Key Frame Interval:</label>
                    <input type="number" 
                           class="option-keyint"
                           value="${videoSettings.options?.['key-int-max'] || 60}"
                           min="1"
                           max="300">
                </div>
            </div>


      


        

            <!-- Preset and Tuning -->
            <div class="settings-group">
                <h4>Presets and Tuning</h4>
                <div class="option-group">
                    <label>Speed Preset:</label>
                    <select class="option-speed-preset">
                        <option value="ultrafast" ${createSelectedString(currentSettings.preset, 'ultrafast')}>Ultra Fast</option>
                        <option value="superfast" ${createSelectedString(currentSettings.preset, 'superfast')}>Super Fast</option>
                        <option value="veryfast" ${createSelectedString(currentSettings.preset, 'veryfast')}>Very Fast</option>
                        <option value="faster" ${createSelectedString(currentSettings.preset, 'faster')}>Faster</option>
                        <option value="fast" ${createSelectedString(currentSettings.preset, 'fast')}>Fast</option>
                        <option value="medium" ${createSelectedString(currentSettings.preset, 'medium')}>Medium</option>
                        <option value="slow" ${createSelectedString(currentSettings.preset, 'slow')}>Slow</option>
                        <option value="slower" ${createSelectedString(currentSettings.preset, 'slower')}>Slower</option>
                        <option value="veryslow" ${createSelectedString(currentSettings.preset, 'veryslow')}>Very Slow</option>
                        <option value="placebo" ${createSelectedString(currentSettings.preset, 'placebo')}>Placebo</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Tune:</label>
                    <select class="option-tune">
                        <option value="stillimage" ${currentSettings.tune?.includes('stillimage') ? 'selected' : ''}>Still Image</option>
                        <option value="fastdecode" ${currentSettings.tune?.includes('fastdecode') ? 'selected' : ''}>Fast Decode</option>
                        <option value="zerolatency" ${currentSettings.tune?.includes('zerolatency') ? 'selected' : ''}>Zero Latency</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Psychovisual Tuning:</label>
                    <select class="option-psy-tune">
                        <option value="none" ${createSelectedString(currentSettings.psyTune, 'none')}>None</option>
                        <option value="film" ${createSelectedString(currentSettings.psyTune, 'film')}>Film</option>
                        <option value="animation" ${createSelectedString(currentSettings.psyTune, 'animation')}>Animation</option>
                        <option value="grain" ${createSelectedString(currentSettings.psyTune, 'grain')}>Grain</option>
                        <option value="psnr" ${createSelectedString(currentSettings.psyTune, 'psnr')}>PSNR</option>
                        <option value="ssim" ${createSelectedString(currentSettings.psyTune, 'ssim')}>SSIM</option>
                    </select>
                </div>
            </div>

            <!-- Frame Control -->
            <div class="settings-group">
                <h4>Frame Control</h4>
                <div class="option-group">
                    <label>Key Frame Interval:</label>
                    <input type="number" 
                           class="option-keyint"
                           ${createOptionString(currentSettings.keyint || 60)}
                           min="1"
                           max="300">
                </div>
                <div class="option-group">
                    <label>B-Frames:</label>
                    <input type="number"
                           class="option-bframes"
                           ${createOptionString(currentSettings.bframes || 0)}
                           min="0"
                           max="16">
                </div>
                <div class="option-checkboxes">
                    <label>
                        <input type="checkbox"
                               class="option-b-adapt"
                               ${currentSettings.bAdapt ? 'checked' : ''}>
                        B-Adapt
                    </label>
                    <label>
                        <input type="checkbox"
                               class="option-b-pyramid"
                               ${currentSettings.bPyramid ? 'checked' : ''}>
                        B-Pyramid
                    </label>
                    <label>
                        <input type="checkbox"
                               class="option-weightb"
                               ${currentSettings.weightb ? 'checked' : ''}>
                        Weighted B-Frames
                    </label>
                </div>
            </div>

            <!-- Analysis -->
            <div class="settings-group">
                <h4>Analysis</h4>
                <div class="option-group">
                    <label>Motion Estimation:</label>
                    <select class="option-me">
                        <option value="dia" ${createSelectedString(currentSettings.me, 'dia')}>Diamond</option>
                        <option value="hex" ${createSelectedString(currentSettings.me, 'hex')}>Hexagon</option>
                        <option value="umh" ${createSelectedString(currentSettings.me, 'umh')}>Uneven Multi-Hex</option>
                        <option value="esa" ${createSelectedString(currentSettings.me, 'esa')}>Exhaustive</option>
                        <option value="tesa" ${createSelectedString(currentSettings.me, 'tesa')}>Transformed Exhaustive</option>
                    </select>
                </div>
                <div class="option-group">
                    <label>Subpixel ME Quality:</label>
                    <input type="number"
                           class="option-subme"
                           ${createOptionString(currentSettings.subme || 1)}
                           min="1"
                           max="10">
                </div>
                <div class="option-checkboxes">
                    <label>
                        <input type="checkbox"
                               class="option-mixed-refs"
                               ${currentSettings.mixedRefs ? 'checked' : ''}>
                        Mixed References
                    </label>
                    <label>
                        <input type="checkbox"
                               class="option-trellis"
                               ${currentSettings.trellis ? 'checked' : ''}>
                        Trellis Quantization
                    </label>
                </div>
            </div>

            <!-- Additional Settings -->
            <div class="settings-group">
                <h4>Additional Settings</h4>
                <div class="option-checkboxes">
                    <label>
                        <input type="checkbox"
                               class="option-cabac"
                               ${currentSettings.cabac ? 'checked' : ''}>
                        CABAC
                    </label>
                    <label>
                        <input type="checkbox"
                               class="option-dct8x8"
                               ${currentSettings.dct8x8 ? 'checked' : ''}>
                        8x8 DCT
                    </label>
                    <label>
                        <input type="checkbox"
                               class="option-interlaced"
                               ${currentSettings.interlaced ? 'checked' : ''}>
                        Interlaced
                    </label>
                    <label>
                        <input type="checkbox"
                               class="option-intra-refresh"
                               ${currentSettings.intraRefresh ? 'checked' : ''}>
                        Intra Refresh
                    </label>
                </div>
                <div class="option-group">
                    <label>Noise Reduction:</label>
                    <input type="number"
                           class="option-noise-reduction"
                           ${createOptionString(currentSettings.noiseReduction || 0)}
                           min="0"
                           max="1000">
                </div>
            </div>


                  <!-- Video Processing -->
            <div class="settings-group">
                <h4>Video Processing</h4>
                <div class="option-group">
                    <label>Resolution:</label>
                    <div class="resolution-inputs">
                        <input type="number" 
                               class="option-width"
                               placeholder="Width"
                               value="${videoSettings.resolution?.width || ''}"
                               min="128"
                               max="7680">
                        <span>x</span>
                        <input type="number" 
                               class="option-height"
                               placeholder="Height"
                               value="${videoSettings.resolution?.height || ''}"
                               min="128"
                               max="4320">
                    </div>
                </div>
                <div class="option-checkboxes">
                    <label>
                        <input type="checkbox" 
                               class="option-deinterlace"
                               ${videoSettings.deinterlace ? 'checked' : ''}>
                        Deinterlace Video
                    </label>
                </div>
            </div>
        `;
    },

    attachEventListeners(context) {

           // Add resolution event listeners
        document.querySelector('.option-width')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-height')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        // Add deinterlace event listener
        document.querySelector('.option-deinterlace')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        // Rate Control
        document.querySelector('.option-pass')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-bitrate')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-vbv-buf-capacity')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        // Quantizer controls
        ['qp-min', 'qp-max', 'qp-step'].forEach(ctrl => {
            document.querySelector(`.option-${ctrl}`)?.addEventListener('change', (e) => {
                context.setUnsavedChanges(true);
            });
        });

        // Presets and Tuning
        document.querySelector('.option-speed-preset')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-tune')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-psy-tune')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        // Frame Control
        document.querySelector('.option-keyint')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-bframes')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        // Checkboxes
        ['b-adapt', 'b-pyramid', 'weightb', 'mixed-refs', 'trellis', 
         'cabac', 'dct8x8', 'interlaced', 'intra-refresh'].forEach(ctrl => {
            document.querySelector(`.option-${ctrl}`)?.addEventListener('change', (e) => {
                context.setUnsavedChanges(true);
            });
        });

        // Analysis
        document.querySelector('.option-me')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-subme')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });

        document.querySelector('.option-noise-reduction')?.addEventListener('change', (e) => {
            context.setUnsavedChanges(true);
        });
    }
};