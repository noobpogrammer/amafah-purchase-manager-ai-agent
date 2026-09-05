import React from 'react';
import { Bot } from 'lucide-react';

export default function AuthShell({ title, subtitle, children, footer }) {
  return (
    <div className="auth-page">
      <div className="auth-card card">
        <div className="auth-brand">
          <div className="logo-icon-wrap">
            <Bot size={22} className="logo-icon" />
          </div>
          <div>
            <h1 className="brand-title">Amafha</h1>
            <span className="brand-subtitle">WhatsApp RFQ Procurement Agent</span>
          </div>
        </div>
        <h2 className="view-title">{title}</h2>
        {subtitle ? <p className="view-description">{subtitle}</p> : null}
        {children}
        {footer ? <div className="auth-footer">{footer}</div> : null}
      </div>
    </div>
  );
}
