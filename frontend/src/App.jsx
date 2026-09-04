import React, { useState, useEffect } from 'react';
import Navbar from './components/Navbar';
import DashboardView from './components/DashboardView';
import SuppliersView from './components/SuppliersView';
import CreateRFQView from './components/CreateRFQView';
import RFQDetailView from './components/RFQDetailView';
import QuotesReportView from './components/QuotesReportView';
import ConversationsView from './components/ConversationsView';
import AgentAttentionView from './components/AgentAttentionView';

import Login from './pages/Login';
import SignUp from './pages/SignUp';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import TeamSettings from './pages/TeamSettings';

import { fetchDashboardMetrics, fetchSuppliers, fetchFlags } from './api';
import { supabase } from './supabaseClient';
import { ensureProfile } from './lib/ensureProfile';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedRfqId, setSelectedRfqId] = useState(null);

  const [metrics, setMetrics] = useState(null);
  const [suppliers, setSuppliers] = useState([]);
  const [pendingFlagsCount, setPendingFlagsCount] = useState(0);

  const [loadingMetrics, setLoadingMetrics] = useState(true);
  const [loadingSuppliers, setLoadingSuppliers] = useState(true);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const [route, setRoute] = useState(window.location.pathname || '/');
  const [profileLoadError, setProfileLoadError] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);

  // Load Dashboard metrics
  const loadMetrics = async () => {
    setLoadingMetrics(true);
    try {
      const data = await fetchDashboardMetrics();
      setMetrics(data);
      setPendingFlagsCount(data.pendingFlags || 0);
    } catch (err) {
      console.error('Error loading dashboard metrics:', err);
    } finally {
      setLoadingMetrics(false);
    }
  };

  // Load Suppliers list
  const loadSuppliers = async () => {
    setLoadingSuppliers(true);
    try {
      const data = await fetchSuppliers();
      setSuppliers(data);
    } catch (err) {
      console.error('Error loading suppliers:', err);
    } finally {
      setLoadingSuppliers(false);
    }
  };

  // Refresh flags count for navbar
  const refreshFlagsCount = async () => {
    try {
      const flags = await fetchFlags();
      const pending = flags.filter((f) => f.status === 'pending').length;
      setPendingFlagsCount(pending);
    } catch (err) {
      console.error('Error refreshing flags count:', err);
    }
  };

  useEffect(() => {
    loadMetrics();
    loadSuppliers();
  }, [refreshTrigger]);

  const handleRFQCreated = () => {
    setRefreshTrigger((prev) => prev + 1);
  };

  // Simple pathname-based router helpers
  const navigate = (path) => {
    window.history.pushState({}, '', path);
    setRoute(path);
  };

  useEffect(() => {
    const onPop = () => setRoute(window.location.pathname || '/');
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  // On app mount, if there's an active session, attempt to ensure profile
  useEffect(() => {
    (async () => {
      setProfileLoadError(null);
      setProfileLoading(true);
      try {
        const { data: sessionRes } = await supabase.auth.getSession();
        const user = sessionRes?.session?.user;
        if (user) {
          await ensureProfile();
        }
      } catch (err) {
        setProfileLoadError(err.message || String(err));
      } finally {
        setProfileLoading(false);
      }
    })();
  }, []);

  // Route: map path to app/tab or standalone pages
  if (route === '/login') {
    return <Login navigate={navigate} onLoginSuccess={() => { setActiveTab('dashboard'); }} />;
  }
  if (route === '/signup') {
    return <SignUp navigate={navigate} />;
  }
  if (route === '/forgot-password') {
    return <ForgotPassword navigate={navigate} />;
  }
  if (route === '/reset-password') {
    return <ResetPassword navigate={navigate} />;
  }
  if (route === '/team') {
    return <TeamSettings navigate={navigate} />;
  }

  // App shell
  return (
    <div className="app-shell">
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        pendingFlagsCount={pendingFlagsCount}
        navigate={navigate}
      />

      <main className="app-container">
        {profileLoading && <div>Loading profile...</div>}
        {profileLoadError && <div className="msg error">{profileLoadError}</div>}

        {activeTab === 'dashboard' && (
          <DashboardView
            metrics={metrics}
            loading={loadingMetrics}
            setActiveTab={setActiveTab}
            setSelectedRfqId={setSelectedRfqId}
          />
        )}

        {activeTab === 'suppliers' && (
          <SuppliersView
            suppliers={suppliers}
            loading={loadingSuppliers}
            refreshSuppliers={loadSuppliers}
          />
        )}

        {activeTab === 'create_rfq' && (
          <CreateRFQView
            onRFQCreated={handleRFQCreated}
            setActiveTab={setActiveTab}
            setSelectedRfqId={setSelectedRfqId}
          />
        )}

        {activeTab === 'rfqs' && (
          <RFQDetailView
            selectedRfqId={selectedRfqId}
            setSelectedRfqId={setSelectedRfqId}
            setActiveTab={setActiveTab}
            refreshTrigger={refreshTrigger}
          />
        )}

        {activeTab === 'quotes_report' && (
          <QuotesReportView
            selectedRfqId={selectedRfqId}
            setSelectedRfqId={setSelectedRfqId}
          />
        )}

        {activeTab === 'conversations' && <ConversationsView />}

        {activeTab === 'flags' && (
          <AgentAttentionView refreshFlagsCount={refreshFlagsCount} />
        )}
      </main>
    </div>
  );
}
