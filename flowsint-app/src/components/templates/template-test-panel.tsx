import { Play, FlaskConical, Loader2, CheckCircle2, XCircle } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import type { ConnectorTestOutcome } from '@/api/template-service'

import type { TemplateConnector, TemplateData } from './template-schema'

export interface TestResult {
  success: boolean
  outcomes?: ConnectorTestOutcome[]
  error?: string
}

interface TemplateTestPanelProps {
  testInput: string
  testResult: TestResult | null
  isTesting: boolean
  hasErrors: boolean
  validationData: TemplateData | undefined
  connector: TemplateConnector | undefined
  onTestInputChange: (value: string) => void
  onRunTest: () => void
}

export function TemplateTestPanel({
  testInput,
  testResult,
  isTesting,
  hasErrors,
  validationData,
  connector,
  onTestInputChange,
  onRunTest
}: TemplateTestPanelProps) {
  return (
    <ResizablePanelGroup direction="horizontal" className="h-full">
      <ResizablePanel defaultSize={30} minSize={20}>
        <div className="h-full p-6 flex flex-col gap-6 overflow-y-auto">
          <div>
            <h3 className="text-lg font-semibold mb-1">Test your connector</h3>
            <p className="text-sm text-muted-foreground">
              Results expose only status and approved provenance.
            </p>
          </div>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="test-input">
                Input Value
                {validationData?.input?.key && (
                  <span className="text-muted-foreground ml-1">({validationData.input.key})</span>
                )}
              </Label>
              <Input
                id="test-input"
                placeholder={`Enter ${validationData?.input?.type || 'value'}...`}
                value={testInput}
                onChange={(event) => onTestInputChange(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && onRunTest()}
              />
            </div>
            {connector && (
              <div className="space-y-1 text-xs text-muted-foreground">
                <p>Destination: <code>{connector.destination_id}</code></p>
                <p>Endpoint: <code>{connector.endpoint_id}</code></p>
                <p>Capability: <code>{connector.capability}</code></p>
              </div>
            )}
            <Button
              onClick={onRunTest}
              disabled={isTesting || hasErrors || !testInput.trim()}
              className="w-full gap-2"
            >
              {isTesting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Testing...
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" />
                  Run test
                </>
              )}
            </Button>
          </div>
          {hasErrors && (
            <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20">
              <p className="text-sm text-destructive">Fix validation errors before testing.</p>
            </div>
          )}
        </div>
      </ResizablePanel>
      <ResizableHandle className="hover:bg-primary/20 active:bg-primary/30 transition-colors data-[resize-handle-state=drag]:bg-primary/30" />
      <ResizablePanel defaultSize={70} minSize={30}>
        <div className="h-full p-4 bg-muted/20 overflow-y-auto">
          {!testResult ? (
            <div className="h-full flex items-center justify-center">
              <div className="text-center text-muted-foreground">
                <FlaskConical className="h-12 w-12 mx-auto mb-4 opacity-20" />
                <p className="text-sm">Run a test to see redacted status here.</p>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <div
                className={`p-2 rounded-lg border ${
                  testResult.success
                    ? 'bg-emerald-500/10 border-emerald-500/20'
                    : 'bg-destructive/10 border-destructive/20'
                }`}
              >
                <div className="flex items-center gap-2">
                  {testResult.success ? (
                    <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                  ) : (
                    <XCircle className="h-5 w-5 text-destructive" />
                  )}
                  <span className="font-medium">
                    {testResult.success ? 'Connector successful' : 'Connector failed'}
                  </span>
                </div>
                {testResult.error && (
                  <p className="text-sm text-destructive mt-1">{testResult.error}</p>
                )}
              </div>
              {testResult.outcomes?.map((outcome, index) => (
                <div key={index} className="rounded-md border p-3 text-sm space-y-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">{outcome.status}</Badge>
                    <span>{outcome.visible_outputs} approved output(s)</span>
                  </div>
                  {outcome.diagnostic && (
                    <p className="text-muted-foreground">{outcome.diagnostic.safe_message}</p>
                  )}
                  {outcome.evidence.map((evidence) => (
                    <p key={`${evidence.destination_id}:${evidence.endpoint_id}`} className="font-mono text-xs text-muted-foreground">
                      {evidence.destination_id}/{evidence.endpoint_id} · {evidence.capability} · policy {evidence.policy_version}
                    </p>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
