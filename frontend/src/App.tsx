import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import { AppLayout } from '@/components/layout/AppLayout'
import { LoginPage } from '@/features/auth/LoginPage'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { ProjetosList } from '@/features/projetos/ProjetosList'
import { ProjetoDetail } from '@/features/projetos/ProjetoDetail'
import { ProjetoForm } from '@/features/projetos/ProjetoForm'
import { AnaliseForm } from '@/features/analises/AnaliseForm'
import { AnaliseDetail } from '@/features/analises/AnaliseDetail'
import { WorkspacePage } from '@/features/workspace/WorkspacePage'

const router = createBrowserRouter([
  {
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/workspace" replace /> },
          { path: '/workspace', element: <WorkspacePage /> },
          { path: '/projetos', element: <ProjetosList /> },
          { path: '/projetos/novo', element: <ProjetoForm /> },
          { path: '/projetos/:id', element: <ProjetoDetail /> },
          { path: '/projetos/:id/analises/novo', element: <AnaliseForm /> },
          { path: '/projetos/:id/analises/:aid', element: <AnaliseDetail /> },
          { path: '/dashboard', element: <DashboardPage /> },
        ],
      },
    ],
  },
  { path: '/login', element: <LoginPage /> },
])

export default function App() {
  return <RouterProvider router={router} />
}
