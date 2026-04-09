import asyncio
import json
import requests
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

TICKET_MP = "579B68CA-DD1D-4304-8CA5-9DDAB86AF83C"
API_MP    = "https://api.mercadopublico.cl/servicios/v1/publico"

def mp_get(endpoint, params={}):
    p = dict(params)
    p["ticket"] = TICKET_MP
    try:
        r = requests.get(f"{API_MP}/{endpoint}", params=p, timeout=60)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

server = Server("mp-bgbcorp")

from mcp import types

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="buscar_licitaciones",
            description="Busca licitaciones en Mercado Publico por region, estado y tipo",
            inputSchema={
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "Region ej: Coquimbo, Antofagasta"},
                    "estado": {"type": "string", "description": "activas, cerrada, adjudicada", "default": "activas"},
                    "tipo":   {"type": "string", "description": "LE, LP, L1, LS (opcional)"},
                    "texto":  {"type": "string", "description": "texto a buscar en nombre (opcional)"},
                },
                "required": []
            }
        ),
        types.Tool(
            name="detalle_licitacion",
            description="Obtiene detalle completo de una licitacion por codigo",
            inputSchema={
                "type": "object",
                "properties": {
                    "codigo": {"type": "string", "description": "Codigo ej: 4295-11-LE26"},
                },
                "required": ["codigo"]
            }
        ),
        types.Tool(
            name="historial_organismo",
            description="Historial y ranking de proveedores de un organismo comprador",
            inputSchema={
                "type": "object",
                "properties": {
                    "nombre_organismo": {"type": "string", "description": "ej: Municipalidad de Ovalle"},
                },
                "required": ["nombre_organismo"]
            }
        ),
        types.Tool(
            name="licitaciones_bgbcorp",
            description="Licitaciones donde BGBCORP SpA ha participado",
            inputSchema={
                "type": "object",
                "properties": {
                    "estado": {"type": "string", "description": "activas, cerrada, adjudicada", "default": "activas"},
                },
                "required": []
            }
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "buscar_licitaciones":
        region = (arguments.get("region") or "").lower()
        estado = arguments.get("estado", "activas")
        tipo   = (arguments.get("tipo") or "").upper()
        texto  = (arguments.get("texto") or "").lower()

        data = mp_get("licitaciones.json", {"estado": estado})
        if "error" in data:
            return [types.TextContent(type="text", text=f"Error API: {data['error']}")]

        listado = data.get("Listado", [])
        total_api = len(listado)

        if tipo:
            listado = [l for l in listado if tipo in l.get("CodigoExterno","").upper()]
        if texto:
            listado = [l for l in listado if texto in l.get("Nombre","").lower()]

        resultados = []
        if region:
            for l in listado[:200]:
                cod = l.get("CodigoExterno","")
                det = mp_get("licitaciones.json", {"codigo": cod})
                if det.get("Listado"):
                    d = det["Listado"][0]
                    reg = d.get("Comprador",{}).get("RegionUnidad","")
                    if region in reg.lower():
                        f = d.get("Fechas") or {}
                        resultados.append({
                            "codigo":    cod,
                            "nombre":    d.get("Nombre",""),
                            "organismo": d.get("Comprador",{}).get("NombreOrganismo",""),
                            "region":    reg.replace("Región de ","").replace("Región del ","").strip(),
                            "monto":     d.get("MontoEstimado"),
                            "cierre":    f.get("FechaCierre",""),
                        })
        else:
            for l in listado[:50]:
                cod = l.get("CodigoExterno","")
                partes = cod.split("-")
                resultados.append({
                    "codigo": cod,
                    "nombre": l.get("Nombre",""),
                    "cierre": l.get("FechaCierre",""),
                    "tipo":   partes[-1] if partes else "",
                })

        texto_resp = f"Encontradas {len(resultados)} licitaciones"
        if region: texto_resp += f" en {region}"
        texto_resp += f" (de {total_api} en API)\n\n"
        texto_resp += json.dumps(resultados[:50], ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=texto_resp)]

    elif name == "detalle_licitacion":
        codigo = arguments.get("codigo","")
        data = mp_get("licitaciones.json", {"codigo": codigo})
        if "error" in data or not data.get("Listado"):
            return [types.TextContent(type="text", text=f"No se encontro: {codigo}")]
        l = data["Listado"][0]
        f = l.get("Fechas") or {}
        reg = l.get("Comprador",{}).get("RegionUnidad","")
        detalle = {
            "codigo":      l.get("CodigoExterno",""),
            "nombre":      l.get("Nombre",""),
            "organismo":   l.get("Comprador",{}).get("NombreOrganismo",""),
            "region":      reg,
            "monto":       l.get("MontoEstimado"),
            "descripcion": l.get("Descripcion","")[:1000],
            "cierre":      f.get("FechaCierre",""),
            "visita":      f.get("FechaVisitaTerreno",""),
            "adjudicacion":f.get("FechaEstimadaAdjudicacion",""),
            "duracion":    l.get("TiempoDuracionContrato",""),
            "estado":      l.get("Estado",""),
        }
        return [types.TextContent(type="text", text=json.dumps(detalle, ensure_ascii=False, indent=2))]

    elif name == "historial_organismo":
        nombre = arguments.get("nombre_organismo","")
        data = mp_get("Empresas/BuscarComprador", {"ticket": TICKET_MP})
        orgs = [o for o in data.get("listaEmpresas",[]) if nombre.lower() in o.get("NombreEmpresa","").lower()]
        if not orgs:
            return [types.TextContent(type="text", text=f"No se encontro: {nombre}")]
        org = orgs[0]
        cod_org = org["CodigoEmpresa"]
        adj = mp_get("licitaciones.json", {"CodigoOrganismo": cod_org, "estado": "adjudicada"})
        act = mp_get("licitaciones.json", {"CodigoOrganismo": cod_org, "estado": "activas"})
        lics_adj = adj.get("Listado",[])
        lics_act = act.get("Listado",[])
        proveedores = {}
        for l in lics_adj:
            if l.get("Adjudicacion"):
                for item in (l["Adjudicacion"].get("listItems") or []):
                    rut = item.get("RutProveedor","")
                    if rut:
                        if rut not in proveedores:
                            proveedores[rut] = {"nombre":item.get("NombreProveedor",""),"adjudicaciones":0,"monto_total":0}
                        proveedores[rut]["adjudicaciones"] += 1
                        proveedores[rut]["monto_total"] += item.get("MontoUnitario",0) or 0
        ranking = sorted(proveedores.values(), key=lambda x: x["adjudicaciones"], reverse=True)[:10]
        resultado = {
            "organismo":  org.get("NombreEmpresa",""),
            "adjudicadas": len(lics_adj),
            "activas":    len(lics_act),
            "ranking":    ranking,
            "activas_detalle": [{"codigo":l.get("CodigoExterno",""),"nombre":l.get("Nombre",""),"cierre":l.get("FechaCierre","")} for l in lics_act[:10]],
        }
        return [types.TextContent(type="text", text=json.dumps(resultado, ensure_ascii=False, indent=2))]

    elif name == "licitaciones_bgbcorp":
        estado = arguments.get("estado","activas")
        data = mp_get("licitaciones.json", {"CodigoProveedor": "1826427"})
        listado = data.get("Listado",[])
        resultado = [{"codigo":l.get("CodigoExterno",""),"nombre":l.get("Nombre",""),"cierre":l.get("FechaCierre","")} for l in listado]
        return [types.TextContent(type="text", text=f"{len(resultado)} licitaciones BGBCORP:\n" + json.dumps(resultado, ensure_ascii=False, indent=2))]

    return [types.TextContent(type="text", text=f"Herramienta desconocida: {name}")]

# ── Servidor HTTP con SSE ──────────────────────────────────────────────────────
sse = SseServerTransport("/messages/")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

async def health(request):
    return JSONResponse({"status": "ok", "server": "mp-bgbcorp"})

app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
    ]
)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
