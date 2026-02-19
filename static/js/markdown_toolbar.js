document.addEventListener('DOMContentLoaded', () => {
    const applyWrap = (textarea, before, after) => {
        if (!textarea) return;
        const start = textarea.selectionStart || 0;
        const end = textarea.selectionEnd || 0;
        const value = textarea.value || '';
        const scrollTop = textarea.scrollTop;

        if (start !== end) {
            const selected = value.slice(start, end);
            const updated = value.slice(0, start) + before + selected + after + value.slice(end);
            textarea.value = updated;
            const newStart = start + before.length;
            const newEnd = newStart + selected.length;
            textarea.setSelectionRange(newStart, newEnd);
        } else {
            const insert = before + after;
            const updated = value.slice(0, start) + insert + value.slice(end);
            textarea.value = updated;
            const cursor = start + before.length;
            textarea.setSelectionRange(cursor, cursor);
        }
        textarea.focus();
        textarea.scrollTop = scrollTop;
    };

    const insertLink = (textarea) => {
        if (!textarea) return;
        const start = textarea.selectionStart || 0;
        const end = textarea.selectionEnd || 0;
        const value = textarea.value || '';
        const scrollTop = textarea.scrollTop;

        if (start !== end) {
            const selected = value.slice(start, end);
            const url = window.prompt('Enter URL', 'https://');
            if (!url) return;
            const insert = `[${selected}](${url})`;
            const updated = value.slice(0, start) + insert + value.slice(end);
            textarea.value = updated;
            const innerStart = start + 1;
            const innerEnd = innerStart + selected.length;
            textarea.setSelectionRange(innerStart, innerEnd);
        } else {
            const text = window.prompt('Link text', 'Link text');
            if (!text) return;
            const url = window.prompt('Enter URL', 'https://');
            if (!url) return;
            const insert = `[${text}](${url})`;
            const updated = value.slice(0, start) + insert + value.slice(end);
            textarea.value = updated;
            const cursor = start + insert.length;
            textarea.setSelectionRange(cursor, cursor);
        }
        textarea.focus();
        textarea.scrollTop = scrollTop;
    };

    document.querySelectorAll('.md-toolbar').forEach(toolbar => {
        const targetId = toolbar.dataset.target;
        const textarea = targetId ? document.getElementById(targetId) : null;
        if (!textarea) return;

        toolbar.addEventListener('click', (event) => {
            const button = event.target.closest('button');
            if (!button) return;
            event.preventDefault();
            const action = button.dataset.md;
            if (action === 'bold') {
                applyWrap(textarea, '**', '**');
            } else if (action === 'italic') {
                applyWrap(textarea, '*', '*');
            } else if (action === 'link') {
                insertLink(textarea);
            }
        });
    });
});
