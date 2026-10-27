'use strict';
// Filtering only changes visibility; links and permissions remain server owned.
{
    const input = document.getElementById('ri-record-search');
    if (input) {
        input.addEventListener('input', () => {
            const query = input.value.trim().toLocaleLowerCase();
            let matches = 0;
            document.querySelectorAll('#ri-record-list .module').forEach(module => {
                let visible = 0;
                module.querySelectorAll('tbody tr').forEach(row => {
                    const name = row.querySelector('th')?.textContent || '';
                    row.hidden = !name.toLocaleLowerCase().includes(query);
                    if (!row.hidden) visible++;
                });
                module.hidden = visible === 0;
                matches += visible;
            });
            document.getElementById('ri-record-empty').hidden = matches > 0;
        });
    }
}
