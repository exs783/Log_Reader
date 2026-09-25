(() => {
    'use strict';

    const state = {
        temporary: null,
        opened: null,
        folders: [],
        editing: null,
        refreshVersion: 0,
    };

    const byId = id => document.getElementById(id);

    async function request(path, options = {}) {
        const response = await fetch(`${API_URL}${path}`, {
            headers: options.body ? {'Content-Type': 'application/json'} : undefined,
            ...options,
        });
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            data = {};
        }
        if (!response.ok || data.success === false) {
            const error = new Error(data.error || `Request failed: ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return data;
    }

    function showCatalogAlert(message) {
        const alert = byId('catalog-alert');
        alert.textContent = message;
        alert.hidden = false;
    }

    function clearCatalogAlert() {
        byId('catalog-alert').hidden = true;
    }

    function showAnalyzer() {
        byId('analyzer-panel').hidden = false;
        byId('catalog-panel').hidden = true;
        document.querySelector('nav').hidden = false;
        document.querySelector('.container').hidden = false;
        byId('upload-alert').hidden = false;
        byId('show-analyzer').classList.add('active');
        byId('show-catalog').classList.remove('active');
    }

    async function showCatalog() {
        byId('analyzer-panel').hidden = true;
        byId('catalog-panel').hidden = false;
        document.querySelector('nav').hidden = true;
        document.querySelector('.container').hidden = true;
        byId('upload-alert').hidden = true;
        byId('show-analyzer').classList.remove('active');
        byId('show-catalog').classList.add('active');
        if (state.opened) renderLogs([state.opened]);
        await refreshCatalog();
    }

    function folderName(folderId) {
        const folder = state.folders.find(item => item.id === folderId);
        return folder ? folder.name : 'Unknown folder';
    }

    function fillFolderSelect(select, includeAll = false) {
        select.replaceChildren();
        if (includeAll) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'All folders';
            select.append(option);
        }
        state.folders.forEach(folder => {
            const option = document.createElement('option');
            option.value = String(folder.id);
            option.textContent = folder.name;
            select.append(option);
        });
    }

    async function loadFolders() {
        const data = await request('/folders');
        state.folders = data.folders;
        fillFolderSelect(byId('log-folder'));
        const selected = byId('catalog-folder-filter').value;
        fillFolderSelect(byId('catalog-folder-filter'), true);
        byId('catalog-folder-filter').value = selected;
        fillFolderSelect(byId('folder-parent'));
        updateFolderContext();
    }

    function folderPath(folderId) {
        const names = [];
        let folder = state.folders.find(item => item.id === folderId);
        while (folder) {
            names.unshift(folder.name);
            folder = state.folders.find(item => item.id === folder.parent_id);
        }
        return names.join(' / ');
    }

    function updateFolderContext() {
        const value = byId('catalog-folder-filter').value;
        const folderId = value ? Number(value) : null;
        byId('catalog-breadcrumb').textContent = folderId ? folderPath(folderId) : 'All folders';
        const editable = folderId !== null && folderId !== 1;
        byId('rename-folder').disabled = !editable;
        byId('delete-folder').disabled = !editable;
    }

    function makeAction(label, handler, className = 'secondary-btn') {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = className;
        button.textContent = label;
        button.addEventListener('click', handler);
        return button;
    }

    function makeLink(label, href) {
        const link = document.createElement('a');
        link.textContent = label;
        link.href = href;
        link.target = '_blank';
        link.rel = 'noopener';
        return link;
    }

    function openLogEditor(log) {
        state.editing = log;
        byId('log-dialog-title').textContent = 'Edit log';
        byId('log-name').value = log.display_name;
        byId('log-folder').value = String(log.folder_id);
        byId('log-tags').value = log.tags.join(', ');
        byId('log-notes').value = log.notes;
        byId('log-form-error').hidden = true;
        byId('log-form').querySelector('[type="submit"]').textContent = 'Save changes';
        byId('log-dialog').showModal();
    }

    async function analyzeLog(log) {
        clearCatalogAlert();
        try {
            const result = await request(`/logs/${encodeURIComponent(log.id)}/analyze`, {
                method: 'POST',
            });
            showAnalyzer();
            displayAnalysis(result);
            opened(log);
        } catch (error) {
            showCatalogAlert(error.message);
        }
    }

    async function deleteLog(log) {
        if (!window.confirm(`Delete ${log.display_name}?`)) return;
        clearCatalogAlert();
        try {
            await request(`/logs/${encodeURIComponent(log.id)}`, {method: 'DELETE'});
            if (state.opened && state.opened.id === log.id) cleared();
            await refreshCatalog();
        } catch (error) {
            showCatalogAlert(error.message);
        }
    }

    function renderLogs(logs) {
        const list = byId('catalog-list');
        list.replaceChildren();
        if (!logs.length) {
            const empty = document.createElement('div');
            empty.className = 'no-data';
            empty.textContent = 'No saved logs match these filters.';
            list.append(empty);
            return;
        }
        logs.forEach(log => {
            const card = document.createElement('article');
            card.className = 'card catalog-log';
            const title = document.createElement('h3');
            title.textContent = log.display_name;
            const meta = document.createElement('div');
            meta.className = 'catalog-meta';
            meta.textContent = `${folderName(log.folder_id)} · ${log.original_filename} · ${log.tags.join(', ')}`;
            const notes = document.createElement('p');
            notes.className = 'catalog-notes';
            notes.textContent = log.notes;
            const actions = document.createElement('div');
            actions.className = 'catalog-actions';
            const textLink = makeLink(
                'View text', `/api/logs/${encodeURIComponent(log.id)}/text`
            );
            textLink.addEventListener('click', event => {
                if (!window.confirm('Large text dumps can exhaust browser memory. '
                    + 'Open anyway? Wait for loading to finish before Ctrl+F or Cmd+F.')) {
                    event.preventDefault();
                }
            });
            actions.append(
                makeLink('Download original', `/api/logs/${encodeURIComponent(log.id)}/download`),
                makeLink('Download text', `/api/logs/${encodeURIComponent(log.id)}/text?download=1`),
                textLink,
                makeAction('Analyze', () => analyzeLog(log)),
                makeAction('Edit log', () => openLogEditor(log)),
                makeAction('Delete log', () => deleteLog(log), 'danger-btn'),
            );
            card.append(title, meta, notes, actions);
            list.append(card);
        });
    }

    async function refreshCatalog() {
        const refreshVersion = ++state.refreshVersion;
        clearCatalogAlert();
        try {
            await loadFolders();
            const params = new URLSearchParams();
            const name = byId('catalog-name-filter').value.trim();
            const tag = byId('catalog-tag-filter').value;
            const folderId = byId('catalog-folder-filter').value;
            if (name) params.set('name', name);
            if (tag) params.set('tag', tag);
            if (folderId) params.set('folder_id', folderId);
            const [logs, tags] = await Promise.all([
                request(`/logs${params.size ? `?${params}` : ''}`),
                request('/tags'),
            ]);
            if (refreshVersion !== state.refreshVersion) return;
            const tagFilter = byId('catalog-tag-filter');
            const selectedTag = tagFilter.value;
            tagFilter.replaceChildren();
            const allTags = document.createElement('option');
            allTags.value = '';
            allTags.textContent = 'All tags';
            tagFilter.append(allTags);
            tags.tags.forEach(tag => {
                const option = document.createElement('option');
                option.value = tag;
                option.textContent = tag;
                tagFilter.append(option);
            });
            tagFilter.value = selectedTag;
            renderLogs(logs.logs);
        } catch (error) {
            if (refreshVersion === state.refreshVersion) showCatalogAlert(error.message);
        }
    }

    function defaultName(logId) {
        const timestamp = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z').replace('T', '_').replaceAll(':', '-');
        return `${timestamp}-${logId.slice(0, 8)}`;
    }

    function openSaveDialog() {
        if (state.opened && !state.temporary) {
            showCatalog();
            return;
        }
        if (!state.temporary) return;
        if (!state.folders.length) {
            const root = document.createElement('option');
            root.value = '1';
            root.textContent = 'Logs';
            byId('log-folder').replaceChildren(root);
        }
        state.editing = null;
        byId('log-dialog-title').textContent = 'Save to catalog';
        byId('log-name').value = defaultName(state.temporary.log_id);
        byId('log-folder').value = '1';
        byId('log-tags').value = '';
        byId('log-notes').value = '';
        byId('log-form-error').hidden = true;
        byId('log-form').querySelector('[type="submit"]').textContent = 'Save log';
        byId('log-dialog').showModal();
        loadFolders().catch(error => {
            const alert = byId('log-form-error');
            alert.textContent = error.message;
            alert.hidden = false;
        });
    }

    async function submitLog(event) {
        event.preventDefault();
        const submit = event.submitter;
        submit.disabled = true;
        byId('save-to-catalog').disabled = true;
        const body = {
            display_name: byId('log-name').value,
            folder_id: Number(byId('log-folder').value),
            tags: byId('log-tags').value.split(',').map(tag => tag.trim()).filter(Boolean),
            notes: byId('log-notes').value,
        };
        try {
            const target = state.editing || state.temporary;
            const data = await request(`/logs/${encodeURIComponent(target.id || target.log_id)}${state.editing ? '' : '/save'}`, {
                method: state.editing ? 'PATCH' : 'POST',
                body: JSON.stringify(body),
            });
            byId('log-dialog').close();
            if (state.editing) {
                if (state.opened && state.opened.id === data.log.id) opened(data.log);
                else byId('save-to-catalog').disabled = !state.opened;
                state.editing = null;
                await refreshCatalog();
            } else {
                opened(data.log);
            }
        } catch (error) {
            const alert = byId('log-form-error');
            alert.textContent = error.status === 404 || error.status === 410
                ? 'This temporary log expired or is missing. Re-upload it before saving.'
                : error.message;
            alert.hidden = false;
            if (!state.editing && (error.status === 404 || error.status === 410)) cleared();
            else byId('save-to-catalog').disabled = !state.temporary && !state.opened;
        } finally {
            submit.disabled = false;
        }
    }

    function uploaded(result) {
        state.temporary = {log_id: result.log_id, expires_at: result.expires_at};
        state.opened = null;
        const save = byId('save-to-catalog');
        save.textContent = 'Save to catalog';
        save.disabled = false;
        byId('temporary-expiry').textContent = `Temporary until ${new Date(result.expires_at).toLocaleString()}`;
    }

    function cleared() {
        state.temporary = null;
        state.opened = null;
        const save = byId('save-to-catalog');
        save.textContent = 'Save to catalog';
        save.disabled = true;
        byId('temporary-expiry').textContent = '';
    }

    function opened(metadata) {
        state.temporary = null;
        state.opened = metadata;
        const save = byId('save-to-catalog');
        save.textContent = 'Open in catalog';
        save.disabled = false;
        byId('temporary-expiry').textContent = '';
    }

    function openFolderDialog(mode) {
        const selectedId = Number(byId('catalog-folder-filter').value || 1);
        state.folderMode = mode;
        byId('folder-dialog-title').textContent = mode === 'create' ? 'Create folder' : 'Rename folder';
        byId('folder-name').value = mode === 'rename' ? folderName(selectedId) : '';
        byId('folder-parent').value = String(selectedId);
        byId('folder-parent-label').hidden = mode === 'rename';
        byId('folder-form-error').hidden = true;
        byId('folder-dialog').showModal();
    }

    async function submitFolder(event) {
        event.preventDefault();
        const selectedId = Number(byId('catalog-folder-filter').value || 1);
        const path = state.folderMode === 'create' ? '/folders' : `/folders/${selectedId}`;
        const body = state.folderMode === 'create'
            ? {name: byId('folder-name').value, parent_id: Number(byId('folder-parent').value)}
            : {name: byId('folder-name').value};
        try {
            const data = await request(path, {method: state.folderMode === 'create' ? 'POST' : 'PATCH', body: JSON.stringify(body)});
            byId('folder-dialog').close();
            await loadFolders();
            if (state.folderMode === 'rename') {
                byId('catalog-folder-filter').value = String(data.folder.id);
            }
            updateFolderContext();
            await refreshCatalog();
        } catch (error) {
            const alert = byId('folder-form-error');
            alert.textContent = error.message;
            alert.hidden = false;
        }
    }

    async function deleteFolder() {
        const folderId = Number(byId('catalog-folder-filter').value);
        if (!folderId || folderId === 1 || !window.confirm(`Delete ${folderName(folderId)}?`)) return;
        clearCatalogAlert();
        try {
            await request(`/folders/${folderId}`, {method: 'DELETE'});
            byId('catalog-folder-filter').value = '';
            await refreshCatalog();
        } catch (error) {
            showCatalogAlert(error.message);
        }
    }

    byId('show-analyzer').addEventListener('click', showAnalyzer);
    byId('show-catalog').addEventListener('click', showCatalog);
    byId('save-to-catalog').addEventListener('click', openSaveDialog);
    byId('cancel-log').addEventListener('click', () => byId('log-dialog').close());
    byId('log-form').addEventListener('submit', submitLog);
    byId('create-folder').addEventListener('click', () => openFolderDialog('create'));
    byId('rename-folder').addEventListener('click', () => openFolderDialog('rename'));
    byId('delete-folder').addEventListener('click', deleteFolder);
    byId('cancel-folder').addEventListener('click', () => byId('folder-dialog').close());
    byId('folder-form').addEventListener('submit', submitFolder);
    byId('catalog-name-filter').addEventListener('input', refreshCatalog);
    byId('catalog-tag-filter').addEventListener('change', refreshCatalog);
    byId('catalog-folder-filter').addEventListener('change', () => {
        updateFolderContext();
        refreshCatalog();
    });

    window.logCatalog = {uploaded, cleared, opened};
})();
