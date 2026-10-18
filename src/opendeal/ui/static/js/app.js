// OpenDeal UI Minimal Vanilla Client Logic

document.addEventListener('DOMContentLoaded', () => {
  // Tab Navigation Handling
  const tabContainers = document.querySelectorAll('.tabs-wrapper');
  tabContainers.forEach(container => {
    const buttons = container.querySelectorAll('.tab-btn');
    const panes = container.querySelectorAll('.tab-pane');

    buttons.forEach(btn => {
      btn.addEventListener('click', () => {
        const targetId = btn.getAttribute('data-tab');
        
        buttons.forEach(b => b.classList.remove('active'));
        panes.forEach(p => p.classList.remove('active'));

        btn.classList.add('active');
        const targetPane = container.querySelector(`#${targetId}`);
        if (targetPane) {
          targetPane.classList.add('active');
        }
      });
    });
  });

  // Table Quick Filter Input
  const filterInputs = document.querySelectorAll('[data-table-filter]');
  filterInputs.forEach(input => {
    const tableId = input.getAttribute('data-table-filter');
    const table = document.getElementById(tableId);
    if (!table) return;

    input.addEventListener('input', () => {
      const query = input.value.toLowerCase().trim();
      const rows = table.querySelectorAll('tbody tr');

      rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        if (text.includes(query)) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      });
    });
  });

  // Safe Action Confirmation Modal / Checks
  const confirmForms = document.querySelectorAll('[data-confirm]');
  confirmForms.forEach(form => {
    form.addEventListener('submit', (e) => {
      const msg = form.getAttribute('data-confirm') || 'Are you sure you want to execute this action?';
      if (!window.confirm(msg)) {
        e.preventDefault();
      }
    });
  });
});
