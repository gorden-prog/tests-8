const $ = id => document.getElementById(id);
const number = n => new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: 1
}).format(n);
let favorites;
try {
    favorites = new Set(JSON.parse(localStorage.getItem('worldatlas-favorites') || '[]'));
} catch {
    favorites = new Set();
}
let onlyFavorites = false,
    requestId = 0,
    timer;
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
} [c]));
async function api(path) {
    const response = await fetch(path);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || 'Ошибка сервера');
    return data;
}

function error(message, retry) {
    $('notice').textContent = message + ' ';
    const button = document.createElement('button');
    button.textContent = 'Повторить';
    button.onclick = retry;
    $('notice').append(button);
}
async function status() {
    const s = await api('/api/status');
    $('stats').innerHTML = `<div><strong>${number(s.countries)}</strong>стран и территорий</div><div><strong>${number(s.population)}</strong>жителей</div>`;
}
async function load() {
    const id = ++requestId;
    $('grid').innerHTML = '<div class="skeleton"></div>'.repeat(8);
    $('notice').textContent = '';
    const [sort, order] = $('sort').value.split(':');
    try {
        let countries = await api('/api/countries?' + new URLSearchParams({
            search: $('search').value,
            region: $('region').value,
            sort,
            order
        }));
        if (id !== requestId) return;
        if (onlyFavorites) countries = countries.filter(c => favorites.has(c.code));
        $('count').textContent = `Найдено: ${countries.length}`;
        $('grid').innerHTML = countries.length ? countries.map(c => `<article class="card"><button class="open" data-code="${c.code}"><img class="flag" src="${escape(c.flag)}" alt="Флаг ${escape(c.name)}" loading="lazy"><div class="info"><h2>${escape(c.name)}</h2><p>Столица · <b>${escape(c.capital || 'Нет данных')}</b></p><p>Регион · <b>${escape(c.region)}</b></p><p>Население · <b>${number(c.population)}</b></p></div></button><button class="heart" data-favorite="${c.code}" aria-label="Избранное: ${escape(c.name)}" aria-pressed="${favorites.has(c.code)}"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/></svg></button></article>`).join('') : '<div class="empty">Ничего не найдено. Измените фильтры или обновите данные.</div>';
    } catch (e) {
        if (id === requestId) {
            $('grid').innerHTML = '';
            error(e.message, load);
        }
    }
}
$('grid').onclick = async e => {
    const heart = e.target.closest('[data-favorite]');
    if (heart) {
        const code = heart.dataset.favorite;
        const active = !favorites.has(code);
        active ? favorites.add(code) : favorites.delete(code);
        heart.setAttribute('aria-pressed', active);
        try {
            localStorage.setItem('worldatlas-favorites', JSON.stringify([...favorites]));
        } catch {
            error('Браузер не разрешает сохранить избранное', load);
        }
        if (onlyFavorites && !active) {
            heart.closest('.card')?.remove();
            const left = document.querySelectorAll('#grid .card').length;
            $('count').textContent = `Найдено: ${left}`;
            if (!left) $('grid').innerHTML = '<div class="empty">Ничего не найдено. Измените фильтры или обновите данные.</div>';
        }
        return;
    }
    const button = e.target.closest('[data-code]');
    if (!button) return;
    $('detail-content').textContent = 'Загрузка…';
    $('detail').showModal();
    try {
        const c = await api('/api/countries/' + button.dataset.code);
        const fields = {
            'Официальное название': c.official,
            'Столица': c.capital,
            'Регион': c.region,
            'Субрегион': c.subregion,
            'Население': number(c.population),
            'Площадь': number(c.area) + ' км²',
            'Плотность': c.density === null ? 'Нет данных' : number(c.density) + ' чел/км²',
            'Языки': c.languages.join(', '),
            'Валюты': c.currencies.join(', '),
            'Часовые пояса': c.timezones.join(', ')
        };
        $('detail-content').innerHTML = `<img src="${escape(c.flag)}" alt="Флаг"><h2>${escape(c.name)}</h2><dl>${Object.entries(fields).map(([k,v])=>`<dt>${k}</dt><dd>${escape(v || 'Нет данных')}</dd>`).join('')}</dl>`;
    } catch (e) {
        $('detail-content').textContent = e.message;
    }
};
$('close').onclick = () => $('detail').close();
$('detail').addEventListener('click', e => {
    if (e.target === $('detail')) $('detail').close();
});
$('search').oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(load, 250);
};
['region', 'sort'].forEach(id => $(id).onchange = load);
$('favorites').onclick = () => {
    onlyFavorites = !onlyFavorites;
    $('favorites').setAttribute('aria-pressed', onlyFavorites);
    load();
};
load();
status().catch(e => error(e.message, () => status()));