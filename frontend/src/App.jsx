import React, { useState, useEffect } from 'react';
import Navbar from './components/Navbar';
import DashboardView from './components/DashboardView';
import SuppliersView from './components/SuppliersView';
import CreateRFQView from './components/CreateRFQView';
import RFQDetailView from './components/RFQDetailView';
import QuotesReportView from './components/QuotesReportView';
import ConversationsView from './components/ConversationsView';
import AgentAttentionView from './components/AgentAttentionView';

import { fetchDashboardMetrics, fetchSuppliers, fetchFlags } from './api';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedRfqId, setSelectedRfqId] = useState(null);

  const [metrics, setMetrics] = useState(null);
  const [suppliers, setSuppliers] = useState([]);
  const [pendingFlagsCount, setPendingFlagsCount] = useState(0);

  const [loadingMetrics, setLoadingMetrics] = useState(true);
  const [loadingSuppliers, setLoadingSuppliers] = useState(true);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

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

  return (
    <div className="app-shell">
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        pendingFlagsCount={pendingFlagsCount}
      />

      <main className="app-container">
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
