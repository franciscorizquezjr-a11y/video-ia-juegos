// Reusable backgrounds; changing a scene preserves trailer, copy and render options.
let setupTrailerScenePresets = [];

async function setupTrailerLoadScenes() {
    const select = document.getElementById('setupTrailerScene');
    if (!select) return;
    try {
        const response = await fetch('/static/setupbackground/scenes.json', {cache: 'no-cache'});
        if (!response.ok) throw new Error('No se pudieron cargar los fondos');
        const scenes = await response.json();
        if (!Array.isArray(scenes)) throw new Error('Lista de fondos no válida');
        setupTrailerScenePresets = scenes.filter(scene => scene && scene.id && scene.background_video && scene.corners);
        select.replaceChildren();
        const custom = document.createElement('option');
        custom.value = ''; custom.textContent = 'Fondo personalizado';
        select.appendChild(custom);
        for (const scene of setupTrailerScenePresets) {
            const option = document.createElement('option');
            option.value = scene.id; option.textContent = scene.label;
            select.appendChild(option);
        }
        const current = setupTrailerScenePresets.find(scene => scene.background_video === setupTrailerState.backgroundVideo);
        select.value = current ? current.id : '';
    } catch (error) {
        console.warn('No se cargaron los escenarios:', error);
    }
}

function setupTrailerSelectScene(id) {
    const scene = setupTrailerScenePresets.find(item => item.id === id);
    if (!scene) return;
    setupTrailerState.backgroundVideo = scene.background_video;
    setupTrailerState.nativeWidth = scene.native_width || 720;
    setupTrailerState.nativeHeight = scene.native_height || 1280;
    setupTrailerState.corners = JSON.parse(JSON.stringify(scene.corners));
    setupTrailerState.defaultCorners = JSON.parse(JSON.stringify(scene.corners));
    const background = document.getElementById('setupBgVideo');
    if (background) {
        background.src = scene.background_video;
        background.load();
    }
    updateSetupTrailerVisuals();
    bindSetupTrailerVideoSync();
    setupTrailerAutoSaveConfig();
}
