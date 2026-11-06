(function () {
    const root = document.getElementById('cutoff-overview');
    if (!root) return;
    const search = root.querySelector('#cutoff-search');
    const status = root.querySelector('#cutoff-status');
    const cards = Array.from(root.querySelectorAll('[data-cutoff-employee]'));
    const buttons = Array.from(root.querySelectorAll('[data-cutoff-filter]'));
    function filterEmployees() {
        const term = search.value.trim().toLowerCase();
        let visible = 0;
        cards.forEach(function (card) {
            const flags = card.dataset.flags.split(' ');
            const matches = status.value === 'all'
                || (status.value === 'attention' ? flags.some(Boolean) : flags.includes(status.value));
            card.hidden = !matches || !card.dataset.search.includes(term);
            if (!card.hidden) visible += 1;
        });
        buttons.forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.cutoffFilter === status.value));
        });
        root.querySelector('#cutoff-result-count').textContent = visible + ' of ' + cards.length + ' employees shown';
        root.querySelector('#cutoff-empty').hidden = visible !== 0 || cards.length === 0;
    }
    search.addEventListener('input', filterEmployees);
    status.addEventListener('change', filterEmployees);
    buttons.forEach(function (button) {
        button.addEventListener('click', function () {
            status.value = button.dataset.cutoffFilter;
            filterEmployees();
        });
    });
    filterEmployees();
})();
