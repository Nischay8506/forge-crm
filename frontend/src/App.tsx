import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MainLayout } from '@/components/layout/MainLayout'
import { LoginPage } from '@/features/auth/LoginPage'
import { HomePage } from '@/pages/HomePage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { UsersPage } from '@/pages/admin/UsersPage'
import { AccountDetailPage } from '@/features/accounts/AccountDetailPage'
import { AccountFormPage } from '@/features/accounts/AccountFormPage'
import { AccountsPage } from '@/features/accounts/AccountsPage'
import { ContactsPage } from '@/features/accounts/ContactsPage'
import { HygienePage } from '@/features/steward/HygienePage'
import { ImportsPage } from '@/features/steward/ImportsPage'
import { MergeDetailPage } from '@/features/steward/MergeDetailPage'
import { MergesPage } from '@/features/steward/MergesPage'
import { ReviewDetailPage } from '@/features/steward/ReviewDetailPage'
import { ReviewQueuePage } from '@/features/steward/ReviewQueuePage'
import { StewardLayout } from '@/features/steward/StewardLayout'
import { AgentDetailPage } from '@/features/agents/AgentDetailPage'
import { AgentsLayout } from '@/features/agents/AgentsLayout'
import { AgentsPage } from '@/features/agents/AgentsPage'
import { AuditPage } from '@/features/agents/AuditPage'
import { RunsPage } from '@/features/agents/RunsPage'
import { ReplayPage } from '@/features/agents/ReplayPage'
import { PoliciesPage } from '@/features/agents/PoliciesPage'
import { StopsPage } from '@/features/agents/StopsPage'
import { MessageTracePage } from '@/features/audit/MessageTracePage'
import { RetentionPage } from '@/features/audit/RetentionPage'
import { ApprovalsPage } from '@/features/decisions/ApprovalsPage'
import { AwayPage } from '@/features/decisions/AwayPage'
import { DeadlinesPage } from '@/features/decisions/DeadlinesPage'
import { DecisionsLayout } from '@/features/decisions/DecisionsLayout'
import { DecisionsPage } from '@/features/decisions/DecisionsPage'
import { CodesPage } from '@/features/integrations/CodesPage'
import { ConnectionDetailPage } from '@/features/integrations/ConnectionDetailPage'
import { ConnectionsPage } from '@/features/integrations/ConnectionsPage'
import { ConnectionWizardPage } from '@/features/integrations/ConnectionWizardPage'
import { CustomersPage } from '@/features/integrations/CustomersPage'
import { IntegrationsLayout } from '@/features/integrations/IntegrationsLayout'
import { McpClientsPage } from '@/features/integrations/McpClientsPage'
import { ChannelSettingsPage } from '@/features/messages/ChannelSettingsPage'
import { ComposePage } from '@/features/messages/ComposePage'
import { LanguageReviewPage } from '@/features/messages/LanguageReviewPage'
import { LanguagesPage } from '@/features/messages/LanguagesPage'
import { MessageDetailPage } from '@/features/messages/MessageDetailPage'
import { MessagesLayout } from '@/features/messages/MessagesLayout'
import { MessagesPage } from '@/features/messages/MessagesPage'
import { TestInboxPage } from '@/features/messages/TestInboxPage'
import { EnquiriesLayout } from '@/features/enquiries/EnquiriesLayout'
import { EnquiryDetailPage } from '@/features/enquiries/EnquiryDetailPage'
import { ManualEnquiryPage } from '@/features/enquiries/ManualEnquiryPage'
import { OtherMailPage } from '@/features/enquiries/OtherMailPage'
import { QueuePage } from '@/features/enquiries/QueuePage'
import { SetupPage } from '@/features/enquiries/SetupPage'
import { TestMailboxPage } from '@/features/enquiries/TestMailboxPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <MainLayout />,
    errorElement: <NotFoundPage />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'admin/users', element: <UsersPage /> },
      {
        path: 'enquiries',
        element: <EnquiriesLayout />,
        children: [
          { index: true, element: <QueuePage /> },
          { path: 'other-mail', element: <OtherMailPage /> },
          { path: 'test-mailbox', element: <TestMailboxPage /> },
          { path: 'setup', element: <SetupPage /> },
        ],
      },
      { path: 'enquiries/new', element: <ManualEnquiryPage /> },
      { path: 'enquiries/:id', element: <EnquiryDetailPage /> },
      { path: 'accounts', element: <AccountsPage /> },
      { path: 'accounts/new', element: <AccountFormPage /> },
      { path: 'accounts/:id', element: <AccountDetailPage /> },
      { path: 'accounts/:id/edit', element: <AccountFormPage /> },
      { path: 'contacts', element: <ContactsPage /> },
      {
        path: 'steward',
        element: <StewardLayout />,
        children: [
          { index: true, element: <Navigate to="review" replace /> },
          { path: 'review', element: <ReviewQueuePage /> },
          { path: 'review/:id', element: <ReviewDetailPage /> },
          { path: 'merges', element: <MergesPage /> },
          { path: 'merges/:id', element: <MergeDetailPage /> },
          { path: 'imports', element: <ImportsPage /> },
          { path: 'hygiene', element: <HygienePage /> },
        ],
      },
      {
        path: 'decisions',
        element: <DecisionsLayout />,
        children: [
          { index: true, element: <DecisionsPage /> },
          { path: 'away', element: <AwayPage /> },
          { path: 'analytics', element: <ApprovalsPage /> },
          { path: 'deadlines', element: <DeadlinesPage /> },
        ],
      },
      {
        path: 'messages',
        element: <MessagesLayout />,
        children: [
          { index: true, element: <MessagesPage /> },
          { path: 'new', element: <ComposePage /> },
          { path: 'test-inbox', element: <TestInboxPage /> },
          { path: 'reviews', element: <LanguageReviewPage /> },
          { path: 'languages', element: <LanguagesPage /> },
          { path: 'settings', element: <ChannelSettingsPage /> },
          { path: ':messageId', element: <MessageDetailPage /> },
        ],
      },
      {
        path: 'agents',
        element: <AgentsLayout />,
        children: [
          { index: true, element: <Navigate to="list" replace /> },
          { path: 'list', element: <AgentsPage /> },
          { path: 'list/:key', element: <AgentDetailPage /> },
          { path: 'policies', element: <PoliciesPage /> },
          { path: 'stops', element: <StopsPage /> },
          { path: 'runs', element: <RunsPage /> },
          { path: 'runs/:runId', element: <ReplayPage /> },
          { path: 'audit', element: <AuditPage /> },
          { path: 'retention', element: <RetentionPage /> },
          { path: 'messages/:messageId', element: <MessageTracePage /> },
        ],
      },
      {
        path: 'integrations',
        element: <IntegrationsLayout />,
        children: [
          { index: true, element: <Navigate to="connections" replace /> },
          { path: 'connections', element: <ConnectionsPage /> },
          { path: 'connections/:id', element: <ConnectionDetailPage /> },
          { path: 'new', element: <ConnectionWizardPage /> },
          { path: 'customers', element: <CustomersPage /> },
          { path: 'codes', element: <CodesPage /> },
          { path: 'mcp', element: <McpClientsPage /> },
        ],
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
