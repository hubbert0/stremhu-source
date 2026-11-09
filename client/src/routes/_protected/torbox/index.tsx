import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { CloudUploadIcon, InfoIcon, RefreshCwIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { toast } from 'sonner'
import * as z from 'zod'

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from '@/shared/components/ui/alert'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/shared/components/ui/card'
import { Input } from '@/shared/components/ui/input'
import { Progress } from '@/shared/components/ui/progress'
import { Separator } from '@/shared/components/ui/separator'
import { Textarea } from '@/shared/components/ui/textarea'
import { AXIOS_INSTANCE } from '@/shared/lib/source/source-client-instance'
import { parseApiError } from '@/shared/lib/utils'

export const TORBOX_UPLOAD_NAME = 'Torrent hozzáadása'

type TorboxTorrent = {
  name: string
  state: string
  progress: number
  finished: boolean
  size: number
  createdAt: string | null
}

type TorboxStatus = {
  enabled: boolean
  error: string | null
  torrents: TorboxTorrent[]
}

type TorboxAddResult = {
  name: string
  hash: string
  message: string
}

const torboxStatus = queryOptions({
  queryKey: ['torbox', 'status'],
  queryFn: async () =>
    (await AXIOS_INSTANCE.get<TorboxStatus>('/api/torbox/status')).data,
  staleTime: 0,
  refetchInterval: 15_000,
})

export const Route = createFileRoute('/_protected/torbox/')({
  component: TorboxUploadRoute,
  validateSearch: z.object({
    ok: z.string().optional(),
    hiba: z.string().optional(),
  }),
  loader: () => ({ breadcrumb: TORBOX_UPLOAD_NAME }),
})

function formatSize(bytes: number): string {
  if (bytes <= 0) return ''
  const gb = bytes / 1024 ** 3
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`
}

function stateLabel(torrent: TorboxTorrent): string {
  const state = torrent.state.toLowerCase()
  if (state.startsWith('uploading') || state.startsWith('seeding')) {
    return 'kész, seedel'
  }
  if (torrent.finished || state.startsWith('completed')) return 'kész'
  if (state.startsWith('downloading')) return 'töltődik'
  if (state.startsWith('stalled')) return 'elakadt (nincs seeder)'
  if (state.startsWith('metadl') || state.startsWith('checking')) {
    return 'előkészítés'
  }
  if (state.startsWith('paused') || state.startsWith('stopped')) {
    return 'leállítva'
  }
  return torrent.state || 'ismeretlen'
}

function TorboxUploadRoute() {
  const { ok, hiba } = Route.useSearch()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [magnet, setMagnet] = useState('')

  const { data: status, isFetching, refetch } = useQuery(torboxStatus)

  // A telefonos megosztásból visszatérve az eredményt jelezzük, majd a címet
  // kitakarítjuk, hogy frissítéskor ne jelenjen meg újra.
  useEffect(() => {
    if (!ok && !hiba) return
    if (ok) toast.success(`Hozzáadva a TorBoxhoz: ${ok}`)
    if (hiba) toast.error(hiba)
    void navigate({ to: '/torbox', search: {}, replace: true })
  }, [ok, hiba, navigate])

  const addTorrent = useMutation({
    mutationFn: async () => {
      const form = new FormData()
      if (file) form.append('file', file)
      else form.append('magnet', magnet.trim())
      return (
        await AXIOS_INSTANCE.post<TorboxAddResult>('/api/torbox/torrents', form)
      ).data
    },
    onSuccess: async (result) => {
      toast.success(`Hozzáadva a TorBoxhoz: ${result.name}`)
      setFile(null)
      setMagnet('')
      if (fileInput.current) fileInput.current.value = ''
      await queryClient.invalidateQueries({ queryKey: torboxStatus.queryKey })
    },
    onError: (error) => {
      toast.error(parseApiError(error))
    },
  })

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!file && !magnet.trim()) {
      toast.error(
        'Válassz egy .torrent fájlt, vagy illessz be egy magnetlinket.',
      )
      return
    }
    addTorrent.mutate()
  }

  return (
    <div className="grid gap-8">
      <Card>
        <CardHeader>
          <CardTitle>{TORBOX_UPLOAD_NAME}</CardTitle>
          <CardDescription>
            Saját .torrent fájl vagy magnetlink a TorBoxra. Pár perc múlva
            megjelenik a LeLibrary TorBox-sorában és a Nuvio felhőnézetében. Ha
            nCore-os, a helykezelés a Hit'n'Run teljesüléséig seedelteti.
          </CardDescription>
        </CardHeader>
        <Separator />
        <CardContent>
          {status && !status.enabled ? (
            <Alert variant="destructive">
              <InfoIcon />
              <AlertTitle>Nincs beállítva TorBox-kulcs</AlertTitle>
              <AlertDescription>
                A szerver .env fájljában add meg a TORBOX_API_KEY értékét.
              </AlertDescription>
            </Alert>
          ) : (
            <form className="grid gap-4" onSubmit={handleSubmit}>
              <div className="grid gap-2">
                <label className="text-sm font-medium" htmlFor="torrent-file">
                  .torrent fájl
                </label>
                <Input
                  id="torrent-file"
                  ref={fileInput}
                  type="file"
                  accept=".torrent,application/x-bittorrent"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-medium" htmlFor="magnet">
                  vagy magnetlink
                </label>
                <Textarea
                  id="magnet"
                  rows={3}
                  placeholder="magnet:?xt=urn:btih:…"
                  value={magnet}
                  disabled={Boolean(file)}
                  onChange={(event) => setMagnet(event.target.value)}
                />
              </div>
              <Button type="submit" disabled={addTorrent.isPending}>
                <CloudUploadIcon />
                {addTorrent.isPending ? 'Küldés…' : 'Hozzáadás a TorBoxhoz'}
              </Button>
              <p className="text-muted-foreground text-sm">
                Telefonon: ha a StremHU-t kiteszed a kezdőképernyőre (böngésző
                menü → Hozzáadás a kezdőképernyőhöz), a letöltött .torrent fájlt
                vagy egy magnetlinket a Megosztás menüből is ide küldheted.
              </p>
            </form>
          )}
        </CardContent>
      </Card>

      {status?.enabled && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between gap-2">
              Legutóbbi torrentek a TorBoxon
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => void refetch()}
                disabled={isFetching}
                aria-label="Frissítés"
              >
                <RefreshCwIcon className={isFetching ? 'animate-spin' : ''} />
              </Button>
            </CardTitle>
          </CardHeader>
          <Separator />
          <CardContent className="grid gap-4">
            {status.error && (
              <Alert variant="destructive">
                <InfoIcon />
                <AlertTitle>{status.error}</AlertTitle>
              </Alert>
            )}
            {status.torrents.length === 0 && !status.error && (
              <p className="text-muted-foreground text-sm">
                Még nincs torrent.
              </p>
            )}
            {status.torrents.map((torrent, index) => (
              <div key={`${torrent.name}-${index}`} className="grid gap-2">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-medium break-all">
                    {torrent.name}
                  </span>
                  <Badge variant={torrent.finished ? 'default' : 'secondary'}>
                    {stateLabel(torrent)}
                  </Badge>
                </div>
                <Progress value={Math.round(torrent.progress * 100)} />
                <span className="text-muted-foreground text-xs">
                  {Math.round(torrent.progress * 100)}%
                  {torrent.size > 0 && ` · ${formatSize(torrent.size)}`}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
