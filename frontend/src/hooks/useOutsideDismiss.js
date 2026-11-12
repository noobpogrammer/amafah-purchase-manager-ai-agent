import { useEffect, useRef } from 'react';

// Preserve multi-selection inside a picker; dismiss when the user leaves it.
export default function useOutsideDismiss(open, onClose) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const outside = event => {
      if (ref.current && !ref.current.contains(event.target)) onClose();
    };
    let tabTimer;
    const escape = event => {
      if (event.key === 'Tab') {
        tabTimer = window.setTimeout(() => {
          if (ref.current && !ref.current.contains(document.activeElement)) onClose();
        }, 0);
        return;
      }
      if (event.key !== 'Escape') return;
      event.preventDefault();
      onClose();
      ref.current?.querySelector('[aria-expanded]')?.focus();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
      window.clearTimeout(tabTimer);
    };
  }, [open, onClose]);
  return ref;
}
