import React from 'react';
import {
  LayoutDashboard,
  Users,
  PlusCircle,
  FileText,
  MessageSquare,
  BarChart3,
  AlertTriangle,
  Bot,
  LogOut
} from 'lucide-react';

export default function Navbar({ activeTab, setActiveTab, pendingFlagsCount, navigate, onSignOut }) {
  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'suppliers', label: 'Suppliers', icon: Users },
    { id: 'create_rfq', label: 'Create RFQ', icon: PlusCircle },
    { id: 'rfqs', label: 'RFQs & Tracking', icon: FileText },
    { id: 'quotes_report', label: 'Quotes & AI Ranking', icon: BarChart3 },
    { id: 'conversations', label: 'WhatsApp Logs', icon: MessageSquare },
    {
      id: 'flags',
      label: 'Agent Attention',
      icon: AlertTriangle,
      badge: pendingFlagsCount > 0 ? pendingFlagsCount : null
    },
  ];

  return (
    <header className="app-header">
      <div className="header-container">
        <div className="brand-logo" onClick={() => { setActiveTab('dashboard'); navigate('/'); }}>
          <div className="logo-icon-wrap">
            <Bot size={22} className="logo-icon" />
          </div>
          <div>
            <h1 className="brand-title">Amafha</h1>
            <span className="brand-subtitle">WhatsApp RFQ Procurement Agent</span>
          </div>
        </div>

        <nav className="nav-menu">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id && window.location.pathname === '/';
            return (
              <button
                key={item.id}
                onClick={() => { setActiveTab(item.id); navigate('/'); }}
                className={`nav-link ${isActive ? 'active' : ''}`}
              >
                <Icon size={18} />
                <span>{item.label}</span>
                {item.badge && <span className="nav-badge">{item.badge}</span>}
              </button>
            );
          })}
        </nav>

        <div className="auth-links">
          <button className={`nav-link small ${window.location.pathname === '/team' ? 'active' : ''}`} onClick={() => navigate('/team')}>Team</button>
          <button className="nav-link small" onClick={onSignOut}>
            <LogOut size={16} />
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
