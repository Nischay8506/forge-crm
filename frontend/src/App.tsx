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
import { PoliciesPage } from '@/features/agents/PoliciesPage'
import { StopsPage } from '@/features/agents/StopsPage'
import { DecisionsPage } from '@/features/decisions/DecisionsPage'

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
      { path: 'decisions', element: <DecisionsPage /> },
      {
        path: 'agents',
        element: <AgentsLayout />,
        children: [
          { index: true, element: <Navigate to="list" replace /> },
          { path: 'list', element: <AgentsPage /> },
          { path: 'list/:key', element: <AgentDetailPage /> },
          { path: 'policies', element: <PoliciesPage /> },
          { path: 'stops', element: <StopsPage /> },
          { path: 'audit', element: <AuditPage /> },
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
