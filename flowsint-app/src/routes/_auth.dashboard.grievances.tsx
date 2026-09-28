import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import { AlertTriangle, Bug, Terminal, Clock, Filter } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PageLayout } from '@/components/layout/page-layout'
import { queryKeys } from '@/api/query-keys'
import { grievanceService, type Grievance } from '@/api/grievance-service'
import Loader from '@/components/loader'
import ErrorState from '@/components/shared/error-state'

export const Route = createFileRoute('/_auth/dashboard/grievances')({
  component: GrievancesPage
})

function GrievancesPage() {
  const {
    data: grievances = [],
    isLoading,
    error,
    refetch
  } = useQuery({
    queryKey: queryKeys.grievances.list,
    queryFn: () => grievanceService.list()
  })

  return (
    <PageLayout
      title="Issues"
      description="Auto-QA grievance reports from client installations."
      isLoading={isLoading}
      loadingComponent={<Loader />}
      error={error}
      errorComponent={
        <ErrorState
          title="Could not load issues"
          description="Something went wrong fetching grievance data."
          error={error}
          onRetry={() => refetch()}
        />
      }
    >
      <div className="w-full">
        {grievances.length === 0 ? (
          <Card className="border-2 border-dashed border-primary/20 bg-gradient-to-br from-primary/5 via-background to-accent/5">
            <CardContent className="flex flex-col items-center justify-center py-20 px-8">
              <div className="text-center space-y-4 max-w-md">
                <div className="flex justify-center">
                  <div className="p-4 bg-primary/10 rounded-full">
                    <AlertTriangle className="w-12 h-12 text-primary opacity-60" />
                  </div>
                </div>
                <h3 className="text-2xl font-bold bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text text-transparent">
                  No Issues Yet
                </h3>
                <p className="text-muted-foreground text-lg leading-relaxed">
                  Auto-QA reports from client tool installations will appear here when issues are detected.
                </p>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card className="overflow-hidden border bg-gradient-to-br from-background to-muted/20">
            <CardHeader className="border-b">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-3 text-xl">
                  <div className="p-2 bg-amber-600 rounded-lg">
                    <Bug className="w-5 h-5 text-white" strokeWidth={1.9} />
                  </div>
                  Grievance Reports
                </CardTitle>
                <Badge variant="secondary" className="px-3 py-1">
                  {grievances.length} {grievances.length === 1 ? 'issue' : 'issues'}
                </Badge>
              </div>
              <CardDescription className="text-base mt-2">
                Tool issues detected and reported by client installations.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="border-b bg-muted/30">
                    <TableHead className="py-4 px-6 text-sm font-semibold">
                      <div className="flex items-center gap-2">
                        <Terminal className="w-4 h-4" />
                        Tool
                      </div>
                    </TableHead>
                    <TableHead className="py-4 text-sm font-semibold">
                      <div className="flex items-center gap-2">
                        <Filter className="w-4 h-4" />
                        Install ID
                      </div>
                    </TableHead>
                    <TableHead className="py-4 text-sm font-semibold">
                      <div className="flex items-center gap-2">
                        <AlertTriangle className="w-4 h-4" />
                        Report
                      </div>
                    </TableHead>
                    <TableHead className="py-4 px-6 text-sm font-semibold text-right">
                      <div className="flex items-center gap-2 justify-end">
                        <Clock className="w-4 h-4" />
                        Reported
                      </div>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {grievances.map((g: Grievance) => (
                    <TableRow
                      key={g.id}
                      className="group hover:bg-gradient-to-r hover:from-amber-500/5 hover:to-accent/5 transition-all duration-200 border-b border-border/50"
                    >
                      <TableCell className="py-5 px-6">
                        <div className="flex items-center gap-4">
                          <div className="p-2 bg-gradient-to-r from-amber-500/10 to-red-500/10 border border-amber-200/20 rounded-lg group-hover:scale-110 transition-transform duration-200">
                            <Terminal className="w-4 h-4 text-amber-600" />
                          </div>
                          <div>
                            <div className="font-semibold text-foreground group-hover:text-amber-600 transition-colors">
                              {g.tool_name}
                            </div>
                            <div className="text-sm text-muted-foreground">report</div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="py-5">
                        <code className="text-xs bg-muted px-2 py-1 rounded font-mono text-muted-foreground">
                          {g.install_id}
                        </code>
                      </TableCell>
                      <TableCell className="py-5 max-w-md">
                        <p className="text-sm text-foreground line-clamp-2">
                          {g.report}
                        </p>
                      </TableCell>
                      <TableCell className="py-5 px-6 text-right">
                        <div className="flex items-center justify-end gap-2 text-sm">
                          <div className="p-1.5 bg-muted rounded-full">
                            <Clock className="w-3 h-3 text-muted-foreground" />
                          </div>
                          <span className="text-muted-foreground whitespace-nowrap">
                            {formatDistanceToNow(new Date(g.created_at), { addSuffix: true })}
                          </span>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </div>
    </PageLayout>
  )
}
