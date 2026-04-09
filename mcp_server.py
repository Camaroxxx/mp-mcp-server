import asyncio
import requests
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

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

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="buscar_licitaciones",
            description="Busca licitaciones en Mercado Publico por region, estado y tipo",
            inputSchema={
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "Nombre de la region (ej: Coquimbo, Antofagasta)"},
                    "estado": {"type": "string", "description": "Estado: activas, cerrada, adjudicada", "default": "activas"},
                    "tipo": {"type": "string", "description": "Tipo: LE, LP, L1, LS (opcional)"},
                    "texto": {"type": "string", "description": "Texto a buscar en el nombre (opcional)"},
                },
                "required": []
            }
        ),
        types.Tool(
            name="detalle_licitacion",
            description="Obtiene el detalle completo de una licitacion por su codigo",
            inputSchema={
                "type": "object",
                "properties": {
                    "codigo": {"type": "string", "description": "Codigo de la licitacion (ej: 4295-11-LE26)"},
                },
                "required": ["codigo"]
            }
        ),
        types.Tool(
            name="historial_organismo",
            description="Obtiene el historial de licitaciones adjudicadas de un organismo y ranking de proveedores",
            inputSchema={
                "type": "object",
                "properties": {
                    "nombre_organismo": {"type": "string", "description": "Nombre del organismo (ej: Municipalidad de Ovalle)"},
                },
                "required": ["nombre_organismo"]
            }
        ),
        types.Tool(
            name="licitaciones_bgbcorp",
            description="Lista las licitaciones donde BGBCORP SpA ha participado",
            inputSchema={
                "type": "object",
                "properties": {
                    "estado": {"type": "string", "description": "Estado: activas, cerrada, adjudicada", "default": "activas"},
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

        # Filtro por tipo y texto en el listado basico
        if tipo:
            listado = [l for l in listado if tipo in l.get("CodigoExterno","").upper()]
        if texto:
            listado = [l for l in listado if texto in l.get("Nombre","").lower()]

        # Si hay filtro de region, necesitamos cargar detalles
        # Limitamos a 200 para no saturar
        resultados = []
        if region:
            muestra = listado[:300]
            for l in muestra:
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
            for l in listado[:100]:
                cod = l.get("CodigoExterno","")
                partes = cod.split("-")
                resultados.append({
                    "codigo":  cod,
                    "nombre":  l.get("Nombre",""),
                    "cierre":  l.get("FechaCierre",""),
                    "tipo":    partes[-1] if partes else "",
                })

        resumen = f"Encontradas {len(resultados)} licitaciones"
        if region:
            resumen += f" en region '{region}'"
        resumen += f" (de {total_api} totales en API)\n\n"
        resumen += json.dumps(resultados[:50], ensure_ascii=False, indent=2)
        return [types.TextContent(type="text", text=resumen)]

    elif name == "detalle_licitacion":
        codigo = arguments.get("codigo","")
        data = mp_get("licitaciones.json", {"codigo": codigo})
        if "error" in data or not data.get("Listado"):
            return [types.TextContent(type="text", text=f"No se encontro la licitacion {codigo}")]
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
        # Buscar organismo
        data = mp_get("Empresas/BuscarComprador", {"ticket": TICKET_MP})
        orgs = data.get("listaEmpresas",[])
        orgs_match = [o for o in orgs if nombre.lower() in o.get("NombreEmpresa","").lower()]
        if not orgs_match:
            return [types.TextContent(type="text", text=f"No se encontro organismo: {nombre}")]
        org = orgs_match[0]
        cod_org = org["CodigoEmpresa"]

        adj = mp_get("licitaciones.json", {"CodigoOrganismo": cod_org, "estado": "adjudicada"})
        act = mp_get("licitaciones.json", {"CodigoOrganismo": cod_org, "estado": "activas"})
        lics_adj = adj.get("Listado",[])
        lics_act = act.get("Listado",[])

        proveedores = {}
        for l in lics_adj:
            if l.get("Adjudicacion"):
                for item in (l["Adjudicacion"].get("listItems") or []):
                    rut    = item.get("RutProveedor","")
                    nombre_p = item.get("NombreProveedor","")
                    monto  = item.get("MontoUnitario",0) or 0
                    if rut:
                        if rut not in proveedores:
                            proveedores[rut] = {"nombre":nombre_p,"adjudicaciones":0,"monto_total":0}
                        proveedores[rut]["adjudicaciones"] += 1
                        proveedores[rut]["monto_total"] += monto

        ranking = sorted(proveedores.values(), key=lambda x: x["adjudicaciones"], reverse=True)[:10]
        resultado = {
            "organismo":    org.get("NombreEmpresa",""),
            "adjudicadas":  len(lics_adj),
            "activas":      len(lics_act),
            "ranking_proveedores": ranking,
            "licitaciones_activas": [{"codigo":l.get("CodigoExterno",""),"nombre":l.get("Nombre",""),"cierre":l.get("FechaCierre","")} for l in lics_act[:10]],
        }
        return [types.TextContent(type="text", text=json.dumps(resultado, ensure_ascii=False, indent=2))]

    elif name == "licitaciones_bgbcorp":
        estado = arguments.get("estado","activas")
        data = mp_get("licitaciones.json", {"CodigoProveedor": "1826427", "estado": estado})
        listado = data.get("Listado",[])
        resultado = [{"codigo":l.get("CodigoExterno",""),"nombre":l.get("Nombre",""),"cierre":l.get("FechaCierre","")} for l in listado]
        return [types.TextContent(type="text", text=f"{len(resultado)} licitaciones BGBCORP:\n" + json.dumps(resultado, ensure_ascii=False, indent=2))]

    return [types.TextContent(type="text", text=f"Herramienta desconocida: {name}")]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
