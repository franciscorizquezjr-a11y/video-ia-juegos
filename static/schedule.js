/**
 * schedule.js — Creative Studio Weekly Time Block Planner
 * Integrates directly into Creative Studio's multi-app architecture.
 * Supports:
 * - Editable schedule from Monday to Sunday (Lunes a Domingo)
 * - 30-minute intervals from 09:00 to 00:00 (midnight / 24:00)
 * - Flexible multi-slot merging (e.g., 2 hours, 1.5 hours, 3 hours)
 * - Interactive Drag-to-Select across consecutive slots
 * - Quick duration shortcuts (+30m, 1h, 1.5h, 2h, 3h, 4h)
 * - Multi-day replication in 1-click
 * - CUSTOM CATEGORIES: create, edit name & color directly from the web interface
 * - Category filtering, search, and time-of-day range filters
 * - Dual persistence: LocalStorage backup + FastAPI backend (/api/schedule & /api/schedule/categories)
 */

(function () {
    'use strict';

    const DAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];
    const TOTAL_SLOTS = 30; // 09:00 to 24:00 (30 half-hour intervals)
    const BASE_HOUR = 9;    // Starts at 09:00

    const DEFAULT_CATEGORIES = [
        { id: 'video', name: 'Vídeo / Edición', color: '#d97757', icon: '🎬' },
        { id: 'dev', name: 'Dev / Código', color: '#3b82f6', icon: '💻' },
        { id: 'record', name: 'Grabación / Stream', color: '#8b5cf6', icon: '🎙️' },
        { id: 'gym', name: 'Salud / Gym', color: '#10b981', icon: '🏋️' },
        { id: 'ocio', name: 'Ocio / Juegos', color: '#f59e0b', icon: '🎮' },
        { id: 'rest', name: 'Descanso / Comida', color: '#06b6d4', icon: '☕' },
        { id: 'general', name: 'General', color: '#9e978e', icon: '📌' }
    ];

    const scheduleState = {
        blocks: [],
        categories: [...DEFAULT_CATEGORIES],
        visibleRange: 'all', // 'all', 'morning', 'afternoon', 'night'
        selectedCategory: 'all',
        searchQuery: '',
        slotHeight: 38,
        // Drag to select state
        isDragging: false,
        dragDay: null,
        dragStartSlot: null,
        dragEndSlot: null,
        editingBlockId: null,
        initialized: false
    };

    /* -------------------------------------------------------------------------
       Category Management Helpers
       ------------------------------------------------------------------------- */
    function getCategory(catId) {
        if (!catId) return { id: 'general', name: 'General', color: '#9e978e', icon: '📌' };
        const found = scheduleState.categories.find(c => c.id === catId);
        if (found) return found;
        return { id: catId, name: catId, color: '#9e978e', icon: '📌' };
    }

    async function loadScheduleCategories() {
        try {
            const resp = await fetch('/api/schedule/categories');
            if (resp.ok) {
                const data = await resp.json();
                if (Array.isArray(data.categories) && data.categories.length > 0) {
                    scheduleState.categories = data.categories;
                    localStorage.setItem('schedule_categories_backup', JSON.stringify(scheduleState.categories));
                }
            }
        } catch (err) {
            console.warn('Could not fetch categories from server, using local backup:', err);
            const backup = localStorage.getItem('schedule_categories_backup');
            if (backup) {
                try {
                    const parsed = JSON.parse(backup);
                    if (Array.isArray(parsed) && parsed.length > 0) {
                        scheduleState.categories = parsed;
                    }
                } catch (e) {}
            }
        }
        renderCategoryToolbarPills();
        renderCategoryModalPills();
    }

    async function saveCategoryToServer(cat) {
        try {
            const resp = await fetch('/api/schedule/categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cat)
            });
            if (resp.ok) {
                const data = await resp.json();
                return data.category || cat;
            }
        } catch (err) {
            console.warn('Failed saving category to server:', err);
        }
        return cat;
    }

    async function deleteCategoryFromServer(catId) {
        try {
            await fetch(`/api/schedule/categories/${encodeURIComponent(catId)}`, { method: 'DELETE' });
        } catch (err) {
            console.warn('Failed deleting category from server:', err);
        }
    }

    async function resetCategoriesOnServer() {
        try {
            const resp = await fetch('/api/schedule/categories/reset', { method: 'POST' });
            if (resp.ok) {
                const data = await resp.json();
                return data.categories || DEFAULT_CATEGORIES;
            }
        } catch (err) {
            console.warn('Failed resetting categories on server:', err);
        }
        return DEFAULT_CATEGORIES;
    }

    function renderCategoryToolbarPills() {
        const container = document.getElementById('schedCategoryFilterPills');
        if (!container) return;

        let html = `<button type="button" class="pill ${scheduleState.selectedCategory === 'all' ? 'active' : ''}" data-cat="all">Todas</button>`;
        scheduleState.categories.forEach(cat => {
            const isActive = scheduleState.selectedCategory === cat.id;
            html += `
                <button type="button" class="pill ${isActive ? 'active' : ''}" data-cat="${escapeHtml(cat.id)}">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background: ${cat.color}; display: inline-block; margin-right: 4px;"></span>
                    ${escapeHtml(cat.icon || '📌')} ${escapeHtml(cat.name)}
                </button>
            `;
        });

        container.innerHTML = html;

        container.querySelectorAll('.pill').forEach(pill => {
            pill.addEventListener('click', () => {
                container.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                scheduleState.selectedCategory = pill.dataset.cat || 'all';
                for (let day = 0; day < 7; day++) {
                    const { minSlot, maxSlot } = getVisibleSlotRange();
                    renderDayBlocks(day, minSlot, maxSlot);
                }
            });
        });
    }

    function renderCategoryModalPills() {
        const container = document.getElementById('schedBlockCategoryRow');
        if (!container) return;

        const currentVal = document.getElementById('schedBlockCategory') ? document.getElementById('schedBlockCategory').value : 'video';
        let html = '';
        scheduleState.categories.forEach(cat => {
            const isActive = currentVal === cat.id;
            html += `
                <button type="button" class="pill ${isActive ? 'active' : ''}" data-cat="${escapeHtml(cat.id)}">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background: ${cat.color}; display: inline-block; margin-right: 4px;"></span>
                    ${escapeHtml(cat.icon || '📌')} ${escapeHtml(cat.name)}
                </button>
            `;
        });
        container.innerHTML = html;

        container.querySelectorAll('.pill').forEach(pill => {
            pill.addEventListener('click', () => {
                setCategoryActive(pill.dataset.cat);
            });
        });
    }

    function renderManageCategoriesList() {
        const listEl = document.getElementById('manageCategoriesList');
        if (!listEl) return;

        let html = '';
        scheduleState.categories.forEach(cat => {
            html += `
                <div class="manage-cat-item" data-cat-id="${escapeHtml(cat.id)}">
                    <div>
                        <input type="text" class="form-input manage-cat-icon-input" value="${escapeHtml(cat.icon || '📌')}" maxlength="4" title="Icono o Emoji">
                    </div>
                    <div>
                        <input type="text" class="form-input manage-cat-name-input" value="${escapeHtml(cat.name)}" placeholder="Nombre de categoría" title="Haz clic para editar nombre">
                    </div>
                    <div class="manage-cat-color-wrap">
                        <input type="color" class="manage-cat-color-input" value="${cat.color}" title="Cambiar color">
                        <span class="cat-hex-label" style="font-size: 11px; font-family: monospace; color: var(--text-muted);">${cat.color}</span>
                    </div>
                    <div>
                        <button type="button" class="btn-cat-del" title="Eliminar categoría">✕</button>
                    </div>
                </div>
            `;
        });

        listEl.innerHTML = html;

        // Bind interactive events for each row
        listEl.querySelectorAll('.manage-cat-item').forEach(row => {
            const catId = row.dataset.catId;
            const iconInput = row.querySelector('.manage-cat-icon-input');
            const nameInput = row.querySelector('.manage-cat-name-input');
            const colorInput = row.querySelector('.manage-cat-color-input');
            const hexLabel = row.querySelector('.cat-hex-label');
            const delBtn = row.querySelector('.btn-cat-del');

            const saveChange = async () => {
                const targetCat = scheduleState.categories.find(c => c.id === catId);
                if (!targetCat) return;
                targetCat.icon = iconInput.value.trim() || '📌';
                targetCat.name = nameInput.value.trim() || 'Sin Nombre';
                targetCat.color = colorInput.value;
                hexLabel.textContent = colorInput.value;

                await saveCategoryToServer(targetCat);
                localStorage.setItem('schedule_categories_backup', JSON.stringify(scheduleState.categories));
                renderCategoryToolbarPills();
                renderCategoryModalPills();
                renderScheduleGrid();
            };

            nameInput.addEventListener('change', saveChange);
            iconInput.addEventListener('change', saveChange);
            colorInput.addEventListener('input', (e) => {
                hexLabel.textContent = e.target.value;
            });
            colorInput.addEventListener('change', saveChange);

            delBtn.addEventListener('click', async () => {
                if (scheduleState.categories.length <= 1) {
                    if (typeof window.showToast === 'function') window.showToast('Debe haber al menos 1 categoría', 'warning');
                    return;
                }
                scheduleState.categories = scheduleState.categories.filter(c => c.id !== catId);
                await deleteCategoryFromServer(catId);
                localStorage.setItem('schedule_categories_backup', JSON.stringify(scheduleState.categories));
                renderManageCategoriesList();
                renderCategoryToolbarPills();
                renderCategoryModalPills();
                renderScheduleGrid();
                if (typeof window.showToast === 'function') window.showToast('Categoría eliminada', 'info');
            });
        });
    }

    async function handleCreateCategory() {
        const nameInput = document.getElementById('newCatName');
        const iconInput = document.getElementById('newCatIcon');
        const colorInput = document.getElementById('newCatColor');

        const name = (nameInput.value || '').trim();
        if (!name) {
            if (typeof window.showToast === 'function') window.showToast('Introduce un nombre para la categoría', 'warning');
            return;
        }

        const icon = (iconInput.value || '').trim() || '📌';
        const color = colorInput.value || '#d97757';
        const id = 'cat_' + Date.now().toString(36) + '_' + Math.random().toString(36).substring(2, 5);

        const newCat = { id, name, color, icon };
        scheduleState.categories.push(newCat);
        await saveCategoryToServer(newCat);
        localStorage.setItem('schedule_categories_backup', JSON.stringify(scheduleState.categories));

        nameInput.value = '';
        renderManageCategoriesList();
        renderCategoryToolbarPills();
        renderCategoryModalPills();
        renderScheduleGrid();

        if (typeof window.showToast === 'function') {
            window.showToast(`Categoría "${name}" creada`, 'success');
        }
    }

    async function handleResetCategories() {
        if (!confirm('¿Restablecer las categorías por defecto?')) return;
        scheduleState.categories = await resetCategoriesOnServer();
        localStorage.setItem('schedule_categories_backup', JSON.stringify(scheduleState.categories));
        renderManageCategoriesList();
        renderCategoryToolbarPills();
        renderCategoryModalPills();
        renderScheduleGrid();
        if (typeof window.showToast === 'function') {
            window.showToast('Categorías restablecidas por defecto', 'info');
        }
    }

    function openManageCategoriesModal() {
        renderManageCategoriesList();
        const modal = document.getElementById('modalManageCategories');
        if (modal) modal.classList.add('active');
    }

    function closeManageCategoriesModal() {
        const modal = document.getElementById('modalManageCategories');
        if (modal) modal.classList.remove('active');
    }

    /* -------------------------------------------------------------------------
       Time & Slot Conversion Helpers
       ------------------------------------------------------------------------- */
    function timeToSlot(str) {
        if (!str) return 0;
        const parts = str.split(':');
        let h = parseInt(parts[0], 10);
        const m = parseInt(parts[1] || '0', 10);
        if (h === 0 && str.startsWith('00:')) {
            h = 24; // Treat 00:00 as midnight (end of day)
        }
        const mins = h * 60 + m;
        const slot = Math.round((mins - (BASE_HOUR * 60)) / 30);
        return Math.max(0, Math.min(TOTAL_SLOTS, slot));
    }

    function slotToTime(slot, isEnd = false) {
        const mins = (BASE_HOUR * 60) + slot * 30;
        let h = Math.floor(mins / 60);
        const m = mins % 60;
        if (h === 24 && !isEnd) h = 0;
        const hStr = (h === 24 && isEnd) ? '24' : String(h).padStart(2, '0');
        const mStr = String(m).padStart(2, '0');
        return `${hStr}:${mStr}`;
    }

    function formatDuration(startSlot, endSlot) {
        const diffSlots = Math.max(1, endSlot - startSlot);
        const mins = diffSlots * 30;
        if (mins < 60) return `${mins} min`;
        const hours = mins / 60;
        return hours % 1 === 0 ? `${hours}h` : `${hours.toFixed(1)}h`;
    }

    function getVisibleSlotRange() {
        switch (scheduleState.visibleRange) {
            case 'morning':
                return { minSlot: 0, maxSlot: 10 }; // 09:00 - 14:00
            case 'afternoon':
                return { minSlot: 10, maxSlot: 22 }; // 14:00 - 20:00
            case 'night':
                return { minSlot: 22, maxSlot: 30 }; // 20:00 - 24:00 (00:00)
            case 'all':
            default:
                return { minSlot: 0, maxSlot: 30 }; // 09:00 - 24:00
        }
    }

    /* -------------------------------------------------------------------------
       Persistence: Load and Save (API + LocalStorage Backup)
       ------------------------------------------------------------------------- */
    async function loadScheduleBlocks() {
        // First, ensure categories are loaded
        await loadScheduleCategories();

        try {
            const resp = await fetch('/api/schedule');
            if (resp.ok) {
                const data = await resp.json();
                scheduleState.blocks = Array.isArray(data.blocks) ? data.blocks : [];
                localStorage.setItem('schedule_blocks_backup', JSON.stringify(scheduleState.blocks));
            } else {
                throw new Error(`Server status ${resp.status}`);
            }
        } catch (err) {
            console.warn('Could not fetch schedule from server, using local backup:', err);
            const backup = localStorage.getItem('schedule_blocks_backup');
            if (backup) {
                try {
                    scheduleState.blocks = JSON.parse(backup) || [];
                } catch (e) {
                    scheduleState.blocks = [];
                }
            }
        }
        renderScheduleGrid();
        updateScheduleStats();
    }
    window.loadScheduleBlocks = loadScheduleBlocks;

    async function saveBlockToServer(blockData) {
        try {
            const resp = await fetch('/api/schedule', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(blockData)
            });
            if (resp.ok) {
                const result = await resp.json();
                return result.block || blockData;
            }
        } catch (err) {
            console.warn('Failed to save block to server, persisting locally:', err);
        }
        return blockData;
    }

    async function deleteBlockFromServer(blockId) {
        try {
            await fetch(`/api/schedule/${encodeURIComponent(blockId)}`, { method: 'DELETE' });
        } catch (err) {
            console.warn('Failed to delete block from server:', err);
        }
    }

    async function clearAllBlocksFromServer() {
        try {
            await fetch('/api/schedule/clear', { method: 'POST' });
        } catch (err) {
            console.warn('Failed to clear schedule on server:', err);
        }
    }

    /* -------------------------------------------------------------------------
       Grid Rendering
       ------------------------------------------------------------------------- */
    function renderScheduleGrid() {
        const bodyEl = document.getElementById('scheduleGridBody');
        if (!bodyEl) return;

        const { minSlot, maxSlot } = getVisibleSlotRange();

        // Build HTML
        let html = '';

        // 1. Time Column
        html += `<div class="schedule-time-column">`;
        for (let slot = minSlot; slot < maxSlot; slot++) {
            const timeLabel = slotToTime(slot);
            const isFullHour = slot % 2 === 0;
            html += `
                <div class="schedule-time-slot-label ${isFullHour ? 'is-full-hour' : 'is-half-hour'}">
                    ${timeLabel}
                </div>
            `;
        }
        html += `</div>`;

        // 2. Day Columns (0 = Lunes to 6 = Domingo)
        for (let day = 0; day < 7; day++) {
            html += `
                <div class="schedule-day-column" data-day="${day}">
                    <!-- Background clickable/draggable slots -->
                    <div class="schedule-slots-layer">
            `;
            for (let slot = minSlot; slot < maxSlot; slot++) {
                const isFullHour = slot % 2 === 0;
                const slotTime = slotToTime(slot);
                html += `
                    <div class="schedule-slot-cell ${isFullHour ? 'is-full-hour' : ''}"
                         data-day="${day}"
                         data-slot="${slot}"
                         data-time="${slotTime}"
                         title="${DAYS[day]} ${slotTime}">
                    </div>
                `;
            }
            html += `
                    </div>
                    <!-- Overlay continuous multi-slot block cards -->
                    <div class="schedule-blocks-layer" id="schedBlocksLayer_${day}">
                        <!-- Rendered by renderDayBlocks -->
                    </div>
                </div>
            `;
        }

        bodyEl.innerHTML = html;

        // Render multi-slot blocks for each day
        for (let day = 0; day < 7; day++) {
            renderDayBlocks(day, minSlot, maxSlot);
        }

        // Bind drag-to-select and slot click events
        bindGridSlotEvents();
    }

    function renderDayBlocks(day, minSlot, maxSlot) {
        const layerEl = document.getElementById(`schedBlocksLayer_${day}`);
        if (!layerEl) return;

        // Filter blocks for this day
        let dayBlocks = scheduleState.blocks.filter(b => Number(b.day) === day);

        // Filter by category
        if (scheduleState.selectedCategory !== 'all') {
            dayBlocks = dayBlocks.filter(b => (b.category || 'general') === scheduleState.selectedCategory);
        }

        // Filter by search
        if (scheduleState.searchQuery.trim()) {
            const q = scheduleState.searchQuery.toLowerCase().trim();
            dayBlocks = dayBlocks.filter(b =>
                (b.title || '').toLowerCase().includes(q) ||
                (b.notes || '').toLowerCase().includes(q)
            );
        }

        let blocksHtml = '';

        dayBlocks.forEach(block => {
            const startSlot = timeToSlot(block.start_time);
            let endSlot = timeToSlot(block.end_time);
            if (endSlot <= startSlot) endSlot = startSlot + 1;

            // Check if block overlaps with visible range
            if (endSlot <= minSlot || startSlot >= maxSlot) {
                return; // Outside visible range
            }

            // Visible clamp
            const visibleStart = Math.max(minSlot, startSlot);
            const visibleEnd = Math.min(maxSlot, endSlot);
            const visibleSlots = visibleEnd - visibleStart;

            const top = (visibleStart - minSlot) * scheduleState.slotHeight + 2;
            const height = Math.max(32, visibleSlots * scheduleState.slotHeight - 4);
            const isCompact = visibleSlots <= 1;
            const durationText = formatDuration(startSlot, endSlot);

            // Dynamic Category info & color
            const catInfo = getCategory(block.category);
            const blockColor = block.color || catInfo.color;

            blocksHtml += `
                <div class="schedule-block-card ${isCompact ? 'is-compact' : ''}"
                     data-block-id="${escapeHtml(block.id)}"
                     style="top: ${top}px; height: ${height}px; --block-color: ${blockColor};"
                     title="${escapeHtml(block.title)} (${block.start_time} - ${block.end_time})">
                    <div class="schedule-block-top">
                        <span class="schedule-block-time">${block.start_time} - ${block.end_time} (${durationText})</span>
                        <div class="schedule-block-actions">
                            <button type="button" class="btn-block-action edit" data-action="edit" title="Editar bloque">✏️</button>
                            <button type="button" class="btn-block-action delete" data-action="delete" title="Eliminar bloque">✕</button>
                        </div>
                    </div>
                    <div class="schedule-block-title">${escapeHtml(block.title)}</div>
                    ${!isCompact ? `
                        <div class="schedule-block-meta">
                            <span class="schedule-block-tag" style="border-left: 2px solid ${blockColor};">
                                ${escapeHtml(catInfo.icon ? catInfo.icon + ' ' : '')}${escapeHtml(catInfo.name)}
                            </span>
                        </div>
                        ${block.notes ? `<div class="schedule-block-notes-snip">${escapeHtml(block.notes)}</div>` : ''}
                    ` : ''}
                </div>
            `;
        });

        layerEl.innerHTML = blocksHtml;

        // Bind clicks on blocks and actions
        layerEl.querySelectorAll('.schedule-block-card').forEach(card => {
            const blockId = card.dataset.blockId;
            const block = scheduleState.blocks.find(b => b.id === blockId);

            card.addEventListener('click', (e) => {
                const actionBtn = e.target.closest('.btn-block-action');
                if (actionBtn) {
                    const action = actionBtn.dataset.action;
                    if (action === 'delete') {
                        e.stopPropagation();
                        confirmDeleteBlock(blockId);
                        return;
                    }
                }
                e.stopPropagation();
                if (block) {
                    openScheduleModal(block);
                }
            });
        });
    }

    /* -------------------------------------------------------------------------
       Drag-to-Select and Click Handlers
       ------------------------------------------------------------------------- */
    function bindGridSlotEvents() {
        const gridBody = document.getElementById('scheduleGridBody');
        if (!gridBody) return;

        gridBody.querySelectorAll('.schedule-slot-cell').forEach(cell => {
            cell.addEventListener('mousedown', (e) => {
                if (e.button !== 0) return; // Left-click only
                e.preventDefault();
                scheduleState.isDragging = true;
                scheduleState.dragDay = parseInt(cell.dataset.day, 10);
                scheduleState.dragStartSlot = parseInt(cell.dataset.slot, 10);
                scheduleState.dragEndSlot = scheduleState.dragStartSlot;
                updateSelectionHighlight();
            });

            cell.addEventListener('mouseenter', () => {
                if (!scheduleState.isDragging) return;
                const cellDay = parseInt(cell.dataset.day, 10);
                if (cellDay !== scheduleState.dragDay) return;
                scheduleState.dragEndSlot = parseInt(cell.dataset.slot, 10);
                updateSelectionHighlight();
            });

            // Single click on slot
            cell.addEventListener('click', (e) => {
                if (scheduleState.suppressNextClick) return;
                const day = parseInt(cell.dataset.day, 10);
                const slot = parseInt(cell.dataset.slot, 10);
                openScheduleModal(null, {
                    day: day,
                    start_time: slotToTime(slot),
                    end_time: slotToTime(slot + 1, true)
                });
            });
        });
    }

    function updateSelectionHighlight() {
        const gridBody = document.getElementById('scheduleGridBody');
        if (!gridBody) return;

        gridBody.querySelectorAll('.schedule-slot-cell.is-selecting').forEach(c => {
            c.classList.remove('is-selecting');
        });

        if (!scheduleState.isDragging || scheduleState.dragDay === null) return;

        const day = scheduleState.dragDay;
        const minS = Math.min(scheduleState.dragStartSlot, scheduleState.dragEndSlot);
        const maxS = Math.max(scheduleState.dragStartSlot, scheduleState.dragEndSlot);

        for (let s = minS; s <= maxS; s++) {
            const cell = gridBody.querySelector(`.schedule-slot-cell[data-day="${day}"][data-slot="${s}"]`);
            if (cell) cell.classList.add('is-selecting');
        }
    }

    function handleGlobalMouseUp(e) {
        if (!scheduleState.isDragging) return;

        const day = scheduleState.dragDay;
        const startSlot = Math.min(scheduleState.dragStartSlot, scheduleState.dragEndSlot);
        const endSlot = Math.max(scheduleState.dragStartSlot, scheduleState.dragEndSlot) + 1;
        const isMulti = (endSlot - startSlot) > 1;

        // Clear selection highlights
        document.querySelectorAll('.schedule-slot-cell.is-selecting').forEach(c => c.classList.remove('is-selecting'));
        scheduleState.isDragging = false;
        scheduleState.dragDay = null;

        if (isMulti) {
            scheduleState.suppressNextClick = true;
            setTimeout(() => { scheduleState.suppressNextClick = false; }, 250);

            // Open modal prefilled with the multi-slot range (e.g. 2 hours)
            openScheduleModal(null, {
                day: day,
                start_time: slotToTime(startSlot),
                end_time: slotToTime(endSlot, true)
            });
        }
    }

    /* -------------------------------------------------------------------------
       Stats Calculation
       ------------------------------------------------------------------------- */
    function updateScheduleStats() {
        let totalMins = 0;
        const dayMinutes = [0, 0, 0, 0, 0, 0, 0];

        scheduleState.blocks.forEach(b => {
            const s = timeToSlot(b.start_time);
            let e = timeToSlot(b.end_time);
            if (e <= s) e = s + 1;
            const mins = (e - s) * 30;
            totalMins += mins;
            const dayIdx = Number(b.day);
            if (dayIdx >= 0 && dayIdx < 7) {
                dayMinutes[dayIdx] += mins;
            }
        });

        const totalHours = totalMins / 60;
        const hoursFormatted = totalHours % 1 === 0 ? `${totalHours}h` : `${totalHours.toFixed(1)}h`;

        const totalHoursEl = document.getElementById('schedStatTotalHours');
        if (totalHoursEl) totalHoursEl.textContent = hoursFormatted;

        const totalBlocksEl = document.getElementById('schedStatTotalBlocks');
        if (totalBlocksEl) totalBlocksEl.textContent = scheduleState.blocks.length;

        // Update day headers
        for (let day = 0; day < 7; day++) {
            const el = document.getElementById(`dayHours_${day}`);
            if (el) {
                const dh = dayMinutes[day] / 60;
                el.textContent = dh % 1 === 0 ? `${dh}h` : `${dh.toFixed(1)}h`;
            }
        }
    }

    /* -------------------------------------------------------------------------
       Modal Controller (Block Editor)
       ------------------------------------------------------------------------- */
    function populateTimeSelectOptions() {
        const selStart = document.getElementById('schedBlockStart');
        const selEnd = document.getElementById('schedBlockEnd');
        if (!selStart || !selEnd) return;

        if (selStart.children.length > 0) return; // Already populated

        let startOptions = '';
        let endOptions = '';

        for (let s = 0; s < TOTAL_SLOTS; s++) {
            const t = slotToTime(s);
            startOptions += `<option value="${t}">${t}</option>`;
        }

        for (let s = 1; s <= TOTAL_SLOTS; s++) {
            const t = slotToTime(s, true);
            endOptions += `<option value="${t}">${t}</option>`;
        }

        selStart.innerHTML = startOptions;
        selEnd.innerHTML = endOptions;

        selStart.addEventListener('change', updateDurationLabelFromSelects);
        selEnd.addEventListener('change', updateDurationLabelFromSelects);
    }

    function updateDurationLabelFromSelects() {
        const selStart = document.getElementById('schedBlockStart');
        const selEnd = document.getElementById('schedBlockEnd');
        const lbl = document.getElementById('schedBlockDurationLabel');
        if (!selStart || !selEnd || !lbl) return;

        const s = timeToSlot(selStart.value);
        let e = timeToSlot(selEnd.value);
        if (e <= s) {
            e = Math.min(TOTAL_SLOTS, s + 1);
            selEnd.value = slotToTime(e, true);
        }

        const diffSlots = e - s;
        const mins = diffSlots * 30;
        const durHours = mins / 60;
        const durStr = durHours % 1 === 0 ? `${durHours}h` : `${durHours.toFixed(1)}h`;
        lbl.textContent = `${durStr} (${diffSlots} hueco${diffSlots > 1 ? 's' : ''})`;

        // Update active duration tag button if matches
        document.querySelectorAll('#schedDurationShortcuts .btn-tag').forEach(btn => {
            const bDur = parseInt(btn.dataset.dur, 10);
            btn.classList.toggle('active', bDur === mins);
        });
    }

    function openScheduleModal(block = null, prefill = null) {
        populateTimeSelectOptions();
        renderCategoryModalPills();

        const modal = document.getElementById('modalScheduleBlock');
        const titleEl = document.getElementById('modalSchedTitle');
        const deleteBtn = document.getElementById('btnSchedDeleteBlock');
        const repeatGroup = document.getElementById('schedRepeatGroup');

        if (!modal) return;

        // Reset repeat checkboxes
        document.querySelectorAll('#schedRepeatGroup input[type="checkbox"]').forEach(cb => {
            cb.checked = false;
        });

        if (block) {
            // Edit existing block
            scheduleState.editingBlockId = block.id;
            titleEl.textContent = 'Editar Bloque de Horario';
            if (deleteBtn) deleteBtn.style.display = 'inline-flex';
            if (repeatGroup) repeatGroup.style.display = 'none'; // Don't show duplicate on edit

            document.getElementById('schedBlockId').value = block.id;
            document.getElementById('schedBlockTitle').value = block.title || '';
            document.getElementById('schedBlockDay').value = String(block.day);
            document.getElementById('schedBlockStart').value = block.start_time || '09:00';
            document.getElementById('schedBlockEnd').value = block.end_time || '09:30';
            document.getElementById('schedBlockNotes').value = block.notes || '';

            setCategoryActive(block.category || (scheduleState.categories[0] ? scheduleState.categories[0].id : 'video'));
            setColorActive(block.color || '#d97757');
        } else {
            // New block
            scheduleState.editingBlockId = null;
            titleEl.textContent = 'Añadir Bloque de Horario';
            if (deleteBtn) deleteBtn.style.display = 'none';
            if (repeatGroup) repeatGroup.style.display = 'block';

            document.getElementById('schedBlockId').value = '';
            document.getElementById('schedBlockTitle').value = '';
            document.getElementById('schedBlockNotes').value = '';

            const defaultDay = prefill && prefill.day !== undefined ? String(prefill.day) : '0';
            const defaultStart = prefill && prefill.start_time ? prefill.start_time : '09:00';
            const defaultEnd = prefill && prefill.end_time ? prefill.end_time : '09:30';

            document.getElementById('schedBlockDay').value = defaultDay;
            document.getElementById('schedBlockStart').value = defaultStart;
            document.getElementById('schedBlockEnd').value = defaultEnd;

            const firstCat = scheduleState.categories[0] ? scheduleState.categories[0].id : 'video';
            setCategoryActive(firstCat);
        }

        updateDurationLabelFromSelects();
        modal.classList.add('active');

        setTimeout(() => {
            const input = document.getElementById('schedBlockTitle');
            if (input) input.focus();
        }, 80);
    }

    function closeScheduleModal() {
        const modal = document.getElementById('modalScheduleBlock');
        if (modal) modal.classList.remove('active');
        scheduleState.editingBlockId = null;
    }

    function setCategoryActive(catKey) {
        const catInput = document.getElementById('schedBlockCategory');
        if (catInput) catInput.value = catKey;

        document.querySelectorAll('#schedBlockCategoryRow .pill').forEach(p => {
            p.classList.toggle('active', p.dataset.cat === catKey);
        });
        const catInfo = getCategory(catKey);
        if (catInfo && !scheduleState.editingBlockId) {
            setColorActive(catInfo.color);
        }
    }

    function setColorActive(colorHex) {
        document.getElementById('schedBlockColor').value = colorHex;
        document.getElementById('schedBlockColorHex').textContent = colorHex;
        const preview = document.getElementById('modalSchedIconPreview');
        if (preview) preview.style.background = colorHex;

        document.querySelectorAll('#schedBlockColorPresets .btn-color-preset').forEach(b => {
            b.classList.toggle('active', b.dataset.color.toLowerCase() === colorHex.toLowerCase());
        });
    }

    async function handleSaveBlock() {
        const title = (document.getElementById('schedBlockTitle').value || '').trim();
        if (!title) {
            if (typeof window.showToast === 'function') {
                window.showToast('Por favor introduce un nombre o actividad para el bloque', 'warning');
            } else {
                alert('Por favor introduce un nombre o actividad para el bloque');
            }
            return;
        }

        const day = parseInt(document.getElementById('schedBlockDay').value, 10);
        const startTime = document.getElementById('schedBlockStart').value;
        let endTime = document.getElementById('schedBlockEnd').value;

        let sSlot = timeToSlot(startTime);
        let eSlot = timeToSlot(endTime);
        if (eSlot <= sSlot) {
            eSlot = Math.min(TOTAL_SLOTS, sSlot + 1);
            endTime = slotToTime(eSlot, true);
        }

        const category = document.getElementById('schedBlockCategory').value || 'video';
        const color = document.getElementById('schedBlockColor').value || '#d97757';
        const notes = (document.getElementById('schedBlockNotes').value || '').trim();

        if (scheduleState.editingBlockId) {
            // Update existing
            const idx = scheduleState.blocks.findIndex(b => b.id === scheduleState.editingBlockId);
            const blockData = {
                id: scheduleState.editingBlockId,
                day: day,
                start_time: startTime,
                end_time: endTime,
                title: title,
                category: category,
                color: color,
                notes: notes
            };

            const saved = await saveBlockToServer(blockData);
            if (idx !== -1) {
                scheduleState.blocks[idx] = saved;
            } else {
                scheduleState.blocks.push(saved);
            }
        } else {
            // Create new
            const newBlockId = `blk_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
            const blockData = {
                id: newBlockId,
                day: day,
                start_time: startTime,
                end_time: endTime,
                title: title,
                category: category,
                color: color,
                notes: notes
            };

            const saved = await saveBlockToServer(blockData);
            scheduleState.blocks.push(saved);

            // Check if repeating on other days
            const repeatDays = [];
            document.querySelectorAll('#schedRepeatGroup input[type="checkbox"]:checked').forEach(cb => {
                const repDay = parseInt(cb.dataset.repDay, 10);
                if (repDay !== day && !isNaN(repDay)) {
                    repeatDays.push(repDay);
                }
            });

            for (const rDay of repeatDays) {
                const cloneId = `blk_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
                const cloneBlock = {
                    ...blockData,
                    id: cloneId,
                    day: rDay
                };
                const savedClone = await saveBlockToServer(cloneBlock);
                scheduleState.blocks.push(savedClone);
            }
        }

        // Backup to localStorage
        localStorage.setItem('schedule_blocks_backup', JSON.stringify(scheduleState.blocks));

        closeScheduleModal();
        renderScheduleGrid();
        updateScheduleStats();

        if (typeof window.showToast === 'function') {
            window.showToast('Bloque guardado correctamente', 'success');
        }
    }

    async function confirmDeleteBlock(blockId) {
        if (!confirm('¿Deseas eliminar este bloque de tiempo?')) return;
        scheduleState.blocks = scheduleState.blocks.filter(b => b.id !== blockId);
        await deleteBlockFromServer(blockId);
        localStorage.setItem('schedule_blocks_backup', JSON.stringify(scheduleState.blocks));
        renderScheduleGrid();
        updateScheduleStats();
        if (typeof window.showToast === 'function') {
            window.showToast('Bloque eliminado', 'info');
        }
    }

    async function handleClearWeek() {
        if (scheduleState.blocks.length === 0) {
            if (typeof window.showToast === 'function') window.showToast('El horario ya está vacío', 'info');
            return;
        }
        if (!confirm('¿Estás seguro de que deseas vaciar todo el horario semanal? Esta acción no se puede deshacer.')) {
            return;
        }
        scheduleState.blocks = [];
        await clearAllBlocksFromServer();
        localStorage.setItem('schedule_blocks_backup', JSON.stringify([]));
        renderScheduleGrid();
        updateScheduleStats();
        if (typeof window.showToast === 'function') {
            window.showToast('Horario semanal vaciado por completo', 'info');
        }
    }

    function handleExportJson() {
        const exportData = {
            version: '1.1',
            exported_at: new Date().toISOString(),
            categories: scheduleState.categories,
            blocks_count: scheduleState.blocks.length,
            blocks: scheduleState.blocks
        };
        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `horario_semanal_${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        if (typeof window.showToast === 'function') {
            window.showToast('Horario exportado en formato JSON', 'success');
        }
    }

    /* -------------------------------------------------------------------------
       Toolbar and UI Event Bindings
       ------------------------------------------------------------------------- */
    function initScheduleApp() {
        // Render UI elements immediately
        renderCategoryToolbarPills();
        renderCategoryModalPills();
        renderScheduleGrid();

        if (scheduleState.initialized) return;
        scheduleState.initialized = true;

        // Global mouseup to release drag-to-select
        document.addEventListener('mouseup', handleGlobalMouseUp);

        // Header actions
        const btnNew = document.getElementById('btnScheduleNewBlock');
        if (btnNew) {
            btnNew.addEventListener('click', (e) => {
                e.preventDefault();
                openScheduleModal();
            });
        }

        const btnClear = document.getElementById('btnScheduleClearWeek');
        if (btnClear) btnClear.addEventListener('click', handleClearWeek);

        const btnExport = document.getElementById('btnScheduleExportJson');
        if (btnExport) btnExport.addEventListener('click', handleExportJson);

        // Category management buttons
        const btnManageCats = document.getElementById('btnManageCategories');
        if (btnManageCats) btnManageCats.addEventListener('click', openManageCategoriesModal);

        const btnQuickManageCats = document.getElementById('btnQuickManageCategories');
        if (btnQuickManageCats) btnQuickManageCats.addEventListener('click', openManageCategoriesModal);

        const btnCloseCatModal = document.getElementById('btnCloseCategoriesModal');
        if (btnCloseCatModal) btnCloseCatModal.addEventListener('click', closeManageCategoriesModal);

        const btnCloseCatModalBtn = document.getElementById('btnCloseCategoriesModalBtn');
        if (btnCloseCatModalBtn) btnCloseCatModalBtn.addEventListener('click', closeManageCategoriesModal);

        const btnCreateCat = document.getElementById('btnCreateCategory');
        if (btnCreateCat) btnCreateCat.addEventListener('click', handleCreateCategory);

        const btnResetCats = document.getElementById('btnResetCategoriesDefault');
        if (btnResetCats) btnResetCats.addEventListener('click', handleResetCategories);

        const newCatColorInput = document.getElementById('newCatColor');
        if (newCatColorInput) {
            newCatColorInput.addEventListener('input', (e) => {
                const hex = document.getElementById('newCatColorHex');
                if (hex) hex.textContent = e.target.value;
            });
        }

        // Visible range pills
        document.querySelectorAll('#schedRangeFilterPills .pill').forEach(pill => {
            pill.addEventListener('click', () => {
                document.querySelectorAll('#schedRangeFilterPills .pill').forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                scheduleState.visibleRange = pill.dataset.range || 'all';
                renderScheduleGrid();
            });
        });

        // Search input
        const searchInput = document.getElementById('schedSearchInput');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                scheduleState.searchQuery = e.target.value || '';
                for (let day = 0; day < 7; day++) {
                    const { minSlot, maxSlot } = getVisibleSlotRange();
                    renderDayBlocks(day, minSlot, maxSlot);
                }
            });
        }

        // Modal close / cancel
        const btnCloseModal = document.getElementById('btnCloseScheduleModal');
        if (btnCloseModal) btnCloseModal.addEventListener('click', closeScheduleModal);

        const btnCancelModal = document.getElementById('btnSchedCancelModal');
        if (btnCancelModal) btnCancelModal.addEventListener('click', closeScheduleModal);

        // Modal save
        const btnSaveModal = document.getElementById('btnSchedSaveBlock');
        if (btnSaveModal) btnSaveModal.addEventListener('click', handleSaveBlock);

        // Modal delete
        const btnDeleteModal = document.getElementById('btnSchedDeleteBlock');
        if (btnDeleteModal) {
            btnDeleteModal.addEventListener('click', () => {
                if (scheduleState.editingBlockId) {
                    confirmDeleteBlock(scheduleState.editingBlockId);
                    closeScheduleModal();
                }
            });
        }

        // Duration shortcut buttons in modal (+30m, 1h, 1.5h, 2h, 3h, 4h)
        document.querySelectorAll('#schedDurationShortcuts .btn-tag').forEach(btn => {
            btn.addEventListener('click', () => {
                const durMins = parseInt(btn.dataset.dur, 10);
                const startVal = document.getElementById('schedBlockStart').value;
                const startSlot = timeToSlot(startVal);
                const endSlot = Math.min(TOTAL_SLOTS, startSlot + durMins / 30);
                document.getElementById('schedBlockEnd').value = slotToTime(endSlot, true);
                updateDurationLabelFromSelects();
            });
        });

        // Color presets inside modal
        document.querySelectorAll('#schedBlockColorPresets .btn-color-preset').forEach(btn => {
            btn.addEventListener('click', () => {
                setColorActive(btn.dataset.color);
            });
        });

        // Color picker input
        const colorInput = document.getElementById('schedBlockColor');
        if (colorInput) {
            colorInput.addEventListener('input', (e) => {
                setColorActive(e.target.value);
            });
        }

        // Backdrop click to close modals
        const modalBlock = document.getElementById('modalScheduleBlock');
        if (modalBlock) {
            modalBlock.addEventListener('click', (e) => {
                if (e.target === modalBlock) closeScheduleModal();
            });
        }
        const modalCats = document.getElementById('modalManageCategories');
        if (modalCats) {
            modalCats.addEventListener('click', (e) => {
                if (e.target === modalCats) closeManageCategoriesModal();
            });
        }

        // ESC key to close modal
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const catModal = document.getElementById('modalManageCategories');
                if (catModal && catModal.classList.contains('active')) {
                    closeManageCategoriesModal();
                    return;
                }
                const modal = document.getElementById('modalScheduleBlock');
                if (modal && modal.classList.contains('active')) {
                    closeScheduleModal();
                }
            }
        });
    }
    window.initScheduleApp = initScheduleApp;

    /* -------------------------------------------------------------------------
       Utilities
       ------------------------------------------------------------------------- */
    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // Auto-initialize when script loads
    function bootSchedule() {
        initScheduleApp();
        loadScheduleBlocks();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootSchedule);
    } else {
        bootSchedule();
    }
})();
