# Amafah Frontend UI Control Inventory & Test Coverage Map

This document catalogues every interactive control, link, button, input, modal, tab, and filter across the Amafah React frontend and maps each to its corresponding Playwright E2E test file and test case.

---

## 1. Global Navigation & App Shell (`App.jsx`, `Navbar.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| App Shell | Public Route Redirect | Route Guard | Unauthenticated user navigating to `/` or `/team` redirects to `/login` | `app-shell.spec.js` | `should redirect unauthenticated users from protected routes to login` |
| App Shell | Authenticated Route Redirect | Route Guard | Authenticated user navigating to `/login` or `/signup` redirects to `/` | `app-shell.spec.js` | `should redirect authenticated users from login/signup to root dashboard` |
| App Shell | Direct URL Refresh | Navigation | Refreshing public auth pages and `/team` maintains correct route | `app-shell.spec.js` | `should support direct page refresh across public and protected routes` |
| App Shell | Browser History (Back/Forward) | Navigation | `popstate` navigation between routes updates active view without crashing | `app-shell.spec.js` | `should handle browser back and forward navigation gracefully` |
| App Shell | Session Check State | UI State | Renders "Checking session..." while auth state is resolving | `app-shell.spec.js` | `should render loading state while session is being verified` |
| App Shell | Profile Load Error | Error State | Renders profile load error message if ensureProfile fails | `app-shell.spec.js` | `should render profile load error message without crashing when ensureProfile fails` |
| Navbar | Brand Logo | Clickable Div | Sets `activeTab='dashboard'` and navigates to `/` | `navbar.spec.js` | `should navigate to dashboard view when clicking brand logo` |
| Navbar | Dashboard Tab | Button | Sets `activeTab='dashboard'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | Suppliers Tab | Button | Sets `activeTab='suppliers'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | Create RFQ Tab | Button | Sets `activeTab='create_rfq'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | RFQs & Tracking Tab | Button | Sets `activeTab='rfqs'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | Quotes & AI Ranking Tab | Button | Sets `activeTab='quotes_report'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | Daily Report Tab | Button | Sets `activeTab='daily_report'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | WhatsApp Logs Tab | Button | Sets `activeTab='conversations'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | Agent Attention Tab | Button | Sets `activeTab='flags'` and navigates to `/` | `navbar.spec.js` | `should switch active tab and view when clicking navigation items` |
| Navbar | Agent Attention Badge | Badge | Displays numeric pending flag badge when count > 0; hides when 0 | `navbar.spec.js` | `should render dynamic pending flag badge count for 0, 1, and >1` |
| Navbar | Team Link | Button | Navigates to `/team` route | `navbar.spec.js` | `should navigate to team settings page` |
| Navbar | Sign Out | Button | Clears auth session, resets state, and redirects to `/login` | `navbar.spec.js` | `should sign out user and redirect to login page` |

---

## 2. Dashboard View (`DashboardView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Dashboard | Create New RFQ Header Button | Button | Switches activeTab to `create_rfq` | `dashboard.spec.js` | `should navigate to create_rfq tab when clicking Create New RFQ button` |
| Dashboard | Review Items Alert Banner Button | Button | Switches activeTab to `flags` when pending items exist | `dashboard.spec.js` | `should show pending flags banner and navigate to flags view when clicked` |
| Dashboard | Active RFQs Metric Card | Card Button | Switches activeTab to `rfqs` | `dashboard.spec.js` | `should navigate to correct views when clicking metric cards` |
| Dashboard | Registered Suppliers Metric Card | Card Button | Switches activeTab to `suppliers` | `dashboard.spec.js` | `should navigate to correct views when clicking metric cards` |
| Dashboard | Quotes Received Metric Card | Card Button | Switches activeTab to `quotes_report` | `dashboard.spec.js` | `should navigate to correct views when clicking metric cards` |
| Dashboard | Pending Agent Flags Metric Card | Card Button | Switches activeTab to `flags` | `dashboard.spec.js` | `should navigate to correct views when clicking metric cards` |
| Dashboard | Manage Suppliers Workflow Step | Clickable Step | Switches activeTab to `suppliers` | `dashboard.spec.js` | `should navigate to correct views when clicking demo workflow steps` |
| Dashboard | Launch RFQ & Auto-Match Workflow Step | Clickable Step | Switches activeTab to `create_rfq` | `dashboard.spec.js` | `should navigate to correct views when clicking demo workflow steps` |
| Dashboard | Track Live Status Workflow Step | Clickable Step | Switches activeTab to `rfqs` | `dashboard.spec.js` | `should navigate to correct views when clicking demo workflow steps` |
| Dashboard | AI Quote Ranking Report Workflow Step | Clickable Step | Switches activeTab to `quotes_report` | `dashboard.spec.js` | `should navigate to correct views when clicking demo workflow steps` |
| Dashboard | View All Logs Button | Button | Switches activeTab to `conversations` | `dashboard.spec.js` | `should navigate to conversations view when clicking View All Logs` |
| Dashboard | Recent WhatsApp Activity List | Feed Item | Displays inbound and outbound recent messages with supplier tags and timestamps | `dashboard.spec.js` | `should render recent messages list or empty state correctly` |

---

## 3. Supplier Directory (`SuppliersView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Suppliers | Add New Supplier Header Button | Button | Opens Add Supplier modal with clean form | `suppliers.spec.js` | `should open add supplier modal and create new supplier successfully` |
| Suppliers | Add First Supplier Empty State Button | Button | Opens Add Supplier modal when list is empty | `suppliers.spec.js` | `should render empty state and allow adding first supplier` |
| Suppliers | Search Input | Text Input | Filters table rows dynamically by supplier name or phone number | `suppliers.spec.js` | `should filter suppliers by name or phone number` |
| Suppliers | Category Filter Dropdown | Select | Filters table rows by category tag; selects "All Categories" | `suppliers.spec.js` | `should filter suppliers by category dropdown` |
| Suppliers | Row Edit Action | Button | Opens Edit Supplier modal populated with existing supplier data | `suppliers.spec.js` | `should open edit modal and update supplier details successfully` |
| Suppliers Modal | Modal Close X Button | Button | Closes modal without saving changes | `suppliers.spec.js` | `should close modal when clicking X or Cancel buttons` |
| Suppliers Modal | Modal Cancel Button | Button | Closes modal without saving changes | `suppliers.spec.js` | `should close modal when clicking X or Cancel buttons` |
| Suppliers Modal | Supplier Name Field | Text Input | Required name validation | `suppliers.spec.js` | `should validate required fields before submitting supplier form` |
| Suppliers Modal | Phone Number Field | Text Input | Required phone validation | `suppliers.spec.js` | `should validate required fields before submitting supplier form` |
| Suppliers Modal | Category Toggle Chips | Chip Buttons | Multi-select / toggle category chips | `suppliers.spec.js` | `should toggle category chips and enforce at least one category` |
| Suppliers Modal | "+ Custom Category" Button | Button | Opens inline custom category input | `suppliers.spec.js` | `should create and select custom category inline` |
| Suppliers Modal | Custom Category Input | Text Input | Captures custom category name; supports Enter key submit | `suppliers.spec.js` | `should create and select custom category inline` |
| Suppliers Modal | Custom Category "Add" Button | Button | Calls `createCustomCategory` API and auto-selects new tag | `suppliers.spec.js` | `should create and select custom category inline` |
| Suppliers Modal | Custom Category "Cancel" Button | Button | Cancels inline custom category creation | `suppliers.spec.js` | `should cancel inline custom category creation` |
| Suppliers Modal | Notes Field | Textarea | Optional notes input | `suppliers.spec.js` | `should save optional notes on supplier` |
| Suppliers Modal | Submit Button (Create/Update) | Button | Submits supplier payload; handles loading and error states | `suppliers.spec.js` | `should handle backend submission failure gracefully in modal` |

---

## 4. Create RFQ — Single & Bulk CSV (`CreateRFQView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Create RFQ | Mode Toggle Button | Button | Toggles between Single RFQ form and Bulk Material Requisition upload | `create-rfq-single.spec.js`, `create-rfq-bulk.spec.js` | `should toggle between single RFQ mode and bulk CSV mode` |
| Single RFQ | Product Name Field | Text Input | Required product name | `create-rfq-single.spec.js` | `should validate required product name` |
| Single RFQ | Category Select Dropdown | Select | Selects category or triggers custom category creation | `create-rfq-single.spec.js` | `should select category and support custom category creation` |
| Single RFQ | Custom Category Inline Form | Form Row | Input, Add button, Cancel button for custom category | `create-rfq-single.spec.js` | `should create and auto-select custom category inline` |
| Single RFQ | Response Deadline Field | Number Input | Required positive hours; shows error text if <= 0 | `create-rfq-single.spec.js` | `should validate positive deadline hours` |
| Single RFQ | Specifications Field | Text Input | Required specifications/notes | `create-rfq-single.spec.js` | `should validate required specifications` |
| Single RFQ | Quantity Field | Number Input | Optional quantity value | `create-rfq-single.spec.js` | `should submit single RFQ with optional quantity and last quote` |
| Single RFQ | Last Quote Field | Number Input | Optional last cost in AED | `create-rfq-single.spec.js` | `should submit single RFQ with optional quantity and last quote` |
| Single RFQ | Acceptable Price Min Field | Number Input | Optional positive min target price | `create-rfq-single.spec.js` | `should validate acceptable price min and max constraints` |
| Single RFQ | Acceptable Price Max Field | Number Input | Optional positive max target price; must be >= min | `create-rfq-single.spec.js` | `should validate acceptable price min and max constraints` |
| Single RFQ | Submit Button | Button | Validates form and submits `/rfq/create`; disables during submission | `create-rfq-single.spec.js` | `should submit single RFQ successfully and render matched suppliers card` |
| Single RFQ Result | Track Live RFQ Status Button | Button | Sets selectedRfqId and switches activeTab to `rfqs` | `create-rfq-single.spec.js` | `should navigate to tracking tab from matched result card` |
| Single RFQ Result | Add Category Suppliers Button | Button | Appears on 0-matched result; switches activeTab to `suppliers` | `create-rfq-single.spec.js` | `should render warning banner and suppliers link when zero suppliers match` |
| Bulk CSV Mode | File Upload Input (`#bulk-rfq-input`) | File Input | Accepts `.csv` files; rejects non-csv files | `create-rfq-bulk.spec.js` | `should reject non-CSV file upload with error message` |
| Bulk CSV Mode | Parsed Rows Table | Data Table | Renders parsed CSV items with descriptions, qty, last quote, and category | `create-rfq-bulk.spec.js` | `should parse valid CSV file and display editable rows` |
| Bulk CSV Mode | Row Selection Checkbox | Checkbox | Toggles inclusion of specific row in bulk creation | `create-rfq-bulk.spec.js` | `should toggle row selection and block submission if none selected` |
| Bulk CSV Mode | Per-Row Product Name Field | Text Input | Edits product name for specific row | `create-rfq-bulk.spec.js` | `should allow editing row fields before bulk submit` |
| Bulk CSV Mode | Per-Row Specs Field | Text Input | Edits specs for specific row | `create-rfq-bulk.spec.js` | `should allow editing row fields before bulk submit` |
| Bulk CSV Mode | Per-Row Quantity Field | Number Input | Edits quantity for specific row | `create-rfq-bulk.spec.js` | `should allow editing row fields before bulk submit` |
| Bulk CSV Mode | Per-Row Category Select | Select | Selects category for specific row | `create-rfq-bulk.spec.js` | `should allow editing row fields before bulk submit` |
| Bulk CSV Mode | Per-Row Deadline Field | Number Input | Sets deadline hours for specific row | `create-rfq-bulk.spec.js` | `should allow editing row fields before bulk submit` |
| Bulk CSV Mode | Default Category Select | Select | Sets default category across bulk batch | `create-rfq-bulk.spec.js` | `should allow editing default category and deadline` |
| Bulk CSV Mode | Default Deadline Field | Number Input | Sets default deadline across bulk batch | `create-rfq-bulk.spec.js` | `should allow editing default category and deadline` |
| Bulk CSV Mode | Confirm Bulk Upload Button | Button | Submits selected rows to `/rfq/bulk-create` with progress loading | `create-rfq-bulk.spec.js` | `should submit bulk RFQs and display creation summary card` |

---

## 5. RFQs & Live Tracking (`RFQDetailView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| RFQs & Tracking | Live Polling Toggle Button | Button | Toggles live 5-second polling interval on and off | `rfq-tracking.spec.js` | `should toggle live polling state` |
| RFQs & Tracking | Filter Tab: All | Segmented Button | Displays all RFQs | `rfq-tracking.spec.js` | `should filter RFQs by All, Active, and Closed tabs` |
| RFQs & Tracking | Filter Tab: Active | Segmented Button | Filters sidebar list to `status === 'active'` | `rfq-tracking.spec.js` | `should filter RFQs by All, Active, and Closed tabs` |
| RFQs & Tracking | Filter Tab: Closed | Segmented Button | Filters sidebar list to `status === 'closed'` or `'cancelled'` | `rfq-tracking.spec.js` | `should filter RFQs by All, Active, and Closed tabs` |
| RFQs & Tracking | RFQ Sidebar Cards | List Item Cards | Selects active RFQ and loads details | `rfq-tracking.spec.js` | `should load RFQ details when clicking sidebar card` |
| RFQs & Tracking | Quotes Report Header Button | Button | Switches activeTab to `quotes_report` | `rfq-tracking.spec.js` | `should navigate to Quotes Report view from detail header` |
| RFQs & Tracking | Trigger AI Ranking Button | Button | Calls `/rfq/:id/rank` and navigates to report view | `rfq-tracking.spec.js` | `should trigger AI ranking and refresh data` |
| RFQs & Tracking | Close RFQ Header Button | Button | Shows inline confirmation prompt ("Close RFQ?") | `rfq-tracking.spec.js` | `should show confirmation prompt before closing RFQ` |
| RFQs & Tracking | "Yes, Close" Button | Button | Calls `/rfq/:id/close?status=closed` and updates status | `rfq-tracking.spec.js` | `should close RFQ when confirming close action` |
| RFQs & Tracking | "Cancel" Close Button | Button | Cancels confirmation prompt without modifying RFQ | `rfq-tracking.spec.js` | `should cancel close action when clicking cancel` |
| RFQs & Tracking | Overview & Quotes Tab Button | Section Button | Switches view section to Overview & Quotes summary | `rfq-tracking.spec.js` | `should switch between Overview and Activity sections` |
| RFQs & Tracking (Admin) | Activity & Audit Log Tab Button | Section Button | Switches view section to Activity timeline (Admin only) | `rfq-audit-admin.spec.js` | `should render Activity & Audit tab for admin and load timeline events` |
| RFQs & Tracking (Admin) | Timeline Refresh Button | Button | Reloads `/rfq/:id/activity` data | `rfq-audit-admin.spec.js` | `should refresh activity timeline when clicking Refresh button` |
| RFQs & Tracking (Admin) | Technical Details Accordion Toggle | Button | Expands/collapses JSON details for timeline event | `rfq-audit-admin.spec.js` | `should expand and collapse technical JSON details on timeline events` |

---

## 6. Quotes & AI Recommendation Report (`QuotesReportView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Quotes Report | Select RFQ Dropdown | Select | Switches selected RFQ and loads comparative data | `quotes-ranking.spec.js` | `should change selected RFQ via dropdown and load comparison data` |
| Quotes Report | Run AI Recommendation Button | Button | Calls `/rfq/:id/rank` and refreshes AI ranking banner | `quotes-ranking.spec.js` | `should run AI ranking recommendation and display hero banner` |
| Quotes Report | AI Recommendation Hero Banner | Banner Card | Displays best offer title, price/specs, and reasoning text | `quotes-ranking.spec.js` | `should render AI recommendation hero card with best offer details` |
| Quotes Report | Received Quotes Table | Data Table | Displays multi-variant quotes, prices, delivery, and raw replies | `quotes-ranking.spec.js` | `should render quotes table with variant tags and best value badge` |
| Quotes Report | AI Comparative Breakdown List | Ranked List | Renders ranked cards (#1, #2...) with per-supplier summary | `quotes-ranking.spec.js` | `should render AI comparative breakdown ranking list` |

---

## 7. Daily Procurement Report (`DailyReportView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Daily Report | Report Date Picker (`#report-date-input`) | Date Input | Selects report target date (constrained to max today) | `daily-report.spec.js` | `should allow selecting report date` |
| Daily Report | Download Word Document Submit Button | Button | Requests `/reports/daily?date=...` and triggers `.docx` download | `daily-report.spec.js` | `should download docx report file on successful submission` |
| Daily Report | No Data Info Banner | Alert Banner | Renders notice when status is `no_data` for selected date | `daily-report.spec.js` | `should display notice banner when no data exists for date` |
| Daily Report | Error Alert Banner | Alert Banner | Renders error message if report generation fails | `daily-report.spec.js` | `should display error alert when backend report generation fails` |

---

## 8. WhatsApp Logs & Conversations (`ConversationsView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Conversations | Supplier Filter Dropdown | Select | Filters transcript by selected supplier ID | `conversations.spec.js` | `should filter conversation transcripts by supplier` |
| Conversations | RFQ Filter Dropdown | Select | Filters transcript by related RFQ ID | `conversations.spec.js` | `should filter conversation transcripts by RFQ` |
| Conversations | Refresh Log Button | Button | Reloads message log transcripts | `conversations.spec.js` | `should reload message logs when clicking Refresh Log button` |
| Conversations | Chat Transcript Messages Feed | Bubble Stream | Renders inbound/outbound bubbles with supplier name, phone, product badge, timestamp | `conversations.spec.js` | `should render inbound and outbound message bubbles correctly` |

---

## 9. Agent Attention & Human Review (`AgentAttentionView.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Agent Attention | Human Instruction Textarea | Textarea | Captures procurement operator guidance/instruction | `agent-attention.spec.js` | `should input operator guidance into instruction textarea` |
| Agent Attention | Send to Supplier Checkbox | Checkbox | Toggles `send_to_supplier` boolean parameter | `agent-attention.spec.js` | `should toggle send to supplier checkbox` |
| Agent Attention | Mark Resolved Button | Button | Calls `/flags/:id/resolve` and reloads flag list | `agent-attention.spec.js` | `should resolve flag directly with Mark Resolved button` |
| Agent Attention | Send Instruction & Resolve Button | Button | Calls `/flags/:id/respond` with instruction payload and reloads | `agent-attention.spec.js` | `should send instruction and resolve flag with Send Instruction button` |
| Agent Attention | Resolved Escalation History List | List | Displays past resolved flags with timestamps | `agent-attention.spec.js` | `should render resolved escalation history list` |

---

## 10. Team Settings & Invitations (`TeamSettings.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Team Settings | Assign Role Select Dropdown | Select | Selects `member` or `admin` role for invite | `team-settings.spec.js` | `should select invite role and generate shareable link` |
| Team Settings | Create Shareable Invite Link Button | Button | Calls `/admin/invite` and displays generated URL | `team-settings.spec.js` | `should generate invite link and display in shareable box` |
| Team Settings | Copy Link Button (Generated) | Button | Copies URL to clipboard and shows "Copied" status | `team-settings.spec.js` | `should copy generated invite link to clipboard` |
| Team Settings | Active Team Members List | List | Renders list of workspace profiles with email and role | `team-settings.spec.js` | `should render active team members list` |
| Team Settings | Active Invitation Links List | List | Renders pending invite tokens with individual copy buttons | `team-settings.spec.js` | `should render pending invite tokens with copy buttons` |
| Team Settings | Member Role Restriction Banner | Alert | Non-admin profile sees access restriction notice | `team-settings.spec.js` | `should restrict member role from generating invite links` |

---

## 11. Authentication Pages (`Login.jsx`, `SignUp.jsx`, `ForgotPassword.jsx`, `ResetPassword.jsx`, `AcceptInvite.jsx`)

| Screen / Component | Control | Type | Expected Action | Test File | Test Case |
|---|---|---|---|---|---|
| Login | Email Field (`#login-email`) | Text Input | Required email input | `auth-login.spec.js` | `should submit login form with valid credentials` |
| Login | Password Field (`#login-password`) | Password Input | Required password input | `auth-login.spec.js` | `should submit login form with valid credentials` |
| Login | Sign In Button | Submit Button | Calls `signInWithPassword`, ensures profile, redirects to `/` | `auth-login.spec.js` | `should submit login form with valid credentials` |
| Login | Sign Up Link | Button | Navigates to `/signup` | `auth-login.spec.js` | `should navigate to signup page` |
| Login | Forgot Password Link | Button | Navigates to `/forgot-password` | `auth-login.spec.js` | `should navigate to forgot password page` |
| SignUp | Email Field (`#signup-email`) | Text Input | Required email input | `auth-signup.spec.js` | `should submit signup form and display verification message` |
| SignUp | Password Field (`#signup-password`) | Password Input | Required password input | `auth-signup.spec.js` | `should submit signup form and display verification message` |
| SignUp | Confirm Password Field (`#signup-confirm`) | Password Input | Validates password match | `auth-signup.spec.js` | `should reject mismatched passwords` |
| SignUp | Sign Up Button | Submit Button | Calls `supabase.auth.signUp` | `auth-signup.spec.js` | `should submit signup form and display verification message` |
| SignUp | Sign In Link | Button | Navigates to `/login` | `auth-signup.spec.js` | `should navigate to login page` |
| SignUp | Invite Query Param (`?token=...`) | Route Param | Auto-redirects to `/accept-invite?token=...` | `auth-signup.spec.js` | `should redirect to accept-invite when token is in query params` |
| ForgotPassword | Email Field (`#forgot-email`) | Text Input | Required email input | `auth-password-reset.spec.js` | `should request password reset email` |
| ForgotPassword | Send Reset Email Button | Submit Button | Calls `resetPasswordForEmail` | `auth-password-reset.spec.js` | `should request password reset email` |
| ForgotPassword | Back to Sign In Link | Button | Navigates to `/login` | `auth-password-reset.spec.js` | `should navigate back to sign in` |
| ResetPassword | New Password Field (`#reset-password`) | Password Input | Required new password input | `auth-password-reset.spec.js` | `should update password successfully` |
| ResetPassword | Set New Password Button | Submit Button | Calls `updateUser({ password })` and navigates to `/login` | `auth-password-reset.spec.js` | `should update password successfully` |
| AcceptInvite | Token Validation Handler | Lifecycle | Validates token via Supabase or `/invite/:token` | `auth-invite.spec.js` | `should validate invitation token and render workspace details` |
| AcceptInvite | Missing Token Error State | Alert View | Displays error and back to sign in link when no token | `auth-invite.spec.js` | `should display error when invitation token is missing or invalid` |
| AcceptInvite | Email Field (`#accept-email`) | Text Input | Required email input | `auth-invite.spec.js` | `should accept invitation and create account` |
| AcceptInvite | Password Field (`#accept-password`) | Password Input | Min 6 characters password validation | `auth-invite.spec.js` | `should validate password length and confirmation match` |
| AcceptInvite | Confirm Password Field (`#accept-confirm`) | Password Input | Confirmation match validation | `auth-invite.spec.js` | `should validate password length and confirmation match` |
| AcceptInvite | Accept Invite & Create Account Button | Submit Button | Calls signUp with tenant metadata and claims token | `auth-invite.spec.js` | `should accept invitation and create account` |
| AcceptInvite | Go to Sign In Success Button | Button | Appears on successful registration; navigates to `/login` | `auth-invite.spec.js` | `should navigate to sign in after successful invitation acceptance` |

---

## 12. Non-Functional & Stress Suites

| Category | Test File | Scope / Invariants Verified |
|---|---|---|
| HTTP Error Matrix | `error-states.spec.js` | Injects 400, 401, 403, 404, 422, 500, network abort, slow responses across all views; ensures no blank screen, no React unhandled crash, and no infinite loading. |
| Double-Click / Race Protection | `race-double-click.spec.js` | Rapidly double-clicks mutation buttons (Create Supplier, Create RFQ, Bulk Upload, AI Ranking, Close RFQ, Resolve Flag, Generate Invite Link, Daily Report); verifies button disablement and absence of duplicate requests. |
| Responsive Viewports | `responsive-smoke.spec.js` | Tests full smoke flow across Desktop (1440x900), Laptop (1280x720), Tablet (768x1024), and Mobile (390x844); verifies modal usability, navigation reachability, and zero layout crashes. |
