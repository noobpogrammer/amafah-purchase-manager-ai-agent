import React from 'react';
import { LayoutDashboard, Users, Plus, FileText, MessageSquare, BarChart3, AlertTriangle, Layers, LogOut, FileDown, Settings } from 'lucide-react';

const groups = [
  { label: 'Workspace', items: [
    { id: 'dashboard', label: 'Overview', icon: LayoutDashboard },
    { id: 'rfqs', label: 'Requests & tracking', icon: FileText },
    { id: 'suppliers', label: 'Suppliers', icon: Users },
    { id: 'quotes_report', label: 'Quotes & rankings', icon: BarChart3 },
  ] },
  { label: 'Activity', items: [
    { id: 'conversations', label: 'Conversations', icon: MessageSquare },
    { id: 'flags', label: 'Agent attention', icon: AlertTriangle },
    { id: 'daily_report', label: 'Daily reports', icon: FileDown },
  ] },
];

export default function Navbar({ activeTab, setActiveTab, pendingFlagsCount, navigate, onSignOut }) {
  const go = id => { setActiveTab(id); navigate('/'); };
  return (
    <header className="app-header">
      <div className="header-container">
        <button className="brand-logo" onClick={() => go('dashboard')} aria-label="Amafah overview">
          <span className="logo-icon-wrap"><Layers size={22} className="logo-icon" aria-hidden="true" /></span>
          <span><span className="brand-title">Amafah<span className="brand-period">.</span></span><span className="brand-subtitle">Procurement workspace</span></span>
        </button>
        <button className={`sidebar-create ${activeTab === 'create_rfq' && window.location.pathname === '/' ? 'selected' : ''}`} onClick={() => go('create_rfq')}>
          <Plus size={18} aria-hidden="true" /><span>Create request</span>
        </button>
        <nav className="nav-menu" aria-label="Main navigation">
          {groups.map(group => (
            <div className="nav-group" key={group.label}>
              <p className="nav-group-label">{group.label}</p>
              {group.items.map(({ id, label, icon: Icon }) => {
                const active = activeTab === id && window.location.pathname === '/';
                return <button key={id} onClick={() => go(id)} className={`nav-link ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined}>
                  <Icon size={18} aria-hidden="true" /><span>{label}</span>
                  {id === 'flags' && pendingFlagsCount > 0 && <span className="nav-badge" aria-label={`${pendingFlagsCount} items need attention`}>{pendingFlagsCount}</span>}
                </button>;
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-note"><span className="sidebar-note-line" /><p>Clear requests.<br />Better buying decisions.</p></div>
        <div className="auth-links">
          <button className={`nav-link ${window.location.pathname === '/team' ? 'active' : ''}`} aria-current={window.location.pathname === '/team' ? 'page' : undefined} onClick={() => navigate('/team')}><Settings size={18} aria-hidden="true" /><span>Team settings</span></button>
          <button className="nav-link" onClick={onSignOut}><LogOut size={18} aria-hidden="true" /><span>Sign out</span></button>
        </div>
      </div>
    </header>
  );
}
