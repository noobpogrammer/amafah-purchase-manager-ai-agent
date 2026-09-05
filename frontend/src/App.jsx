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

const PUBLIC_PATHS = ['/login', '/signup', '/forgot-password', '/reset-password'];

function pathOnly(route) {
  return (route || '/').split('?')[0];
}

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
  const [session, setSession] = useState(undefined);
  const [profileLoadError, setProfileLoadError] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);

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

  const refreshFlagsCount = async () => {
    try {
      const flags = await fetchFlags();
      const pending = flags.filter((f) => f.status === 'pending').length;
      setPendingFlagsCount(pending);
    } catch (err) {
      console.error('Error refreshing flags count:', err);
    }
  };

  const handleRFQCreated = () => {
    setRefreshTrigger((prev) => prev + 1);
  };

  const navigate = (path) => {
    window.history.pushState({}, '', path);
    setRoute(pathOnly(path));
  };

  useEffect(() => {
    const onPop = () => setRoute(window.location.pathname || '/');
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    let mounted = true;
    (async () => {
      const { data: sessionRes } = await supabase.auth.getSession();
      if (!mounted) return;
      setSession(sessionRes?.session ?? null);
    })();

    const { data: sub } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession ?? null);
    });

    return () => {
      mounted = false;
      sub?.subscription?.unsubscribe?.();
    };
  }, []);

  useEffect(() => {
    if (session === undefined) return;
    const path = pathOnly(route);
    const isPublic = PUBLIC_PATHS.includes(path);
    if (!session && !isPublic) {
      navigate('/login');
      return;
    }
    if (session && (path === '/login' || path === '/signup')) {
      navigate('/');
    }
  }, [session, route]);

  useEffect(() => {
    if (!session) return;
    (async () => {
      setProfileLoadError(null);
      setProfileLoading(true);
      try {
        await ensureProfile();
      } catch (err) {
        setProfileLoadError(err.message || String(err));
      } finally {
        setProfileLoading(false);
      }
    })();
  }, [session]);

  useEffect(() => {
    if (!session) return;
    loadMetrics();
    loadSuppliers();
  }, [refreshTrigger, session]);

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    setActiveTab('dashboard');
    setMetrics(null);
    setSuppliers([]);
    navigate('/login');
  };

  if (session === undefined) {
    return (
      <div className="auth-page">
        <div className="loading-state">Checking session...</div>
      </div>
    );
  }

  const path = pathOnly(route);
  const isPublic = PUBLIC_PATHS.includes(path);

  if (!session && isPublic) {
    if (path === '/login') {
      return (
        <Login
          navigate={navigate}
          onLoginSuccess={() => {
            setActiveTab('dashboard');
          }}
        />
      );
    }
    if (path === '/signup') {
      return <SignUp navigate={navigate} />;
    }
    if (path === '/forgot-password') {
      return <ForgotPassword navigate={navigate} />;
    }
    if (path === '/reset-password') {
      return <ResetPassword navigate={navigate} />;
    }
  }

  if (!session) {
    return (
      <div className="auth-page">
        <div className="loading-state">Redirecting to sign in...</div>
      </div>
    );
  }

  const navbar = (
    <Navbar
      activeTab={activeTab}
      setActiveTab={setActiveTab}
      pendingFlagsCount={pendingFlagsCount}
      navigate={navigate}
      onSignOut={handleSignOut}
    />
  );

  if (path === '/team') {
    return (
      <div className="app-shell">
        {navbar}
        <main className="app-container">
          <TeamSettings navigate={navigate} />
        </main>
      </div>
    );
  }

  if (path === '/forgot-password') {
    return <ForgotPassword navigate={navigate} />;
  }
  if (path === '/reset-password') {
    return <ResetPassword navigate={navigate} />;
  }

  return (
    <div className="app-shell">
      {navbar}

      <main className="app-container">
        {profileLoading && <div className="loading-state">Loading profile...</div>}
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
