import { NextRequest } from "next/server";
import { proxyConnectorRequest } from "./_proxy";

export const GET = (request: NextRequest) => proxyConnectorRequest(request);
export const POST = (request: NextRequest) => proxyConnectorRequest(request);
