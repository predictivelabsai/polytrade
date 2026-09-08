import type { TypedData as PolyTradeTypedData } from "@polytrade/contracts";
import type { EIP1193Provider } from "viem";
import {
  createWalletClient,
  custom,
  getAddress,
  type Address,
  type Hex,
  type TypedData,
  type TypedDataDomain,
  type WalletClient,
} from "viem";
import { polygon } from "viem/chains";

export interface ConnectedWallet {
  address: Address;
  client: WalletClient;
  provider: EIP1193Provider;
}

const POLYGON_CHAIN_PARAMS = {
  chainId: "0x89",
  chainName: "Polygon",
  nativeCurrency: { name: "POL", symbol: "POL", decimals: 18 },
  rpcUrls: ["https://polygon-rpc.com"],
  blockExplorerUrls: ["https://polygonscan.com"],
};

const POLYGON_CHAIN_ID = 137;

function declined(error: unknown): boolean {
  return (error as { code?: number }).code === 4001;
}

/**
 * Make sure the wallet sits on Polygon (chain 137) — PolyTrade signs every
 * payload (auth challenges and order intents) on Polygon, so a wallet left on
 * another network (e.g. Optimism) would fail with a cryptic viem
 * "Provided chainId must match the active chainId" error.
 */
async function ensurePolygonChain(provider: EIP1193Provider): Promise<void> {
  let active: unknown;
  try {
    active = await provider.request({ method: "eth_chainId" });
  } catch {
    return; // cannot read the active chain — let signTypedData surface any real problem
  }
  const activeChainId = typeof active === "string" ? Number.parseInt(active, active.startsWith("0x") ? 16 : 10) : Number(active);
  if (activeChainId === POLYGON_CHAIN_ID) return;

  const switchToPolygon = () =>
    provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: "0x89" }] });
  try {
    await switchToPolygon();
  } catch (caught) {
    if (declined(caught)) {
      throw new Error("You declined the switch to the Polygon network. PolyTrade signs on Polygon — switch your wallet to Polygon and try again.");
    }
    try {
      // 4902 = chain not added to the wallet yet; add it, then switch.
      await provider.request({ method: "wallet_addEthereumChain", params: [POLYGON_CHAIN_PARAMS] });
      await switchToPolygon();
    } catch (retry) {
      if (declined(retry)) {
        throw new Error("You declined adding the Polygon network. PolyTrade signs on Polygon — switch your wallet to Polygon and try again.");
      }
      throw new Error("Could not switch your wallet to the Polygon network. Switch to Polygon in your wallet manually, then try again.");
    }
  }
}

export async function connectWallet(): Promise<ConnectedWallet> {
  if (!window.ethereum) {
    throw new Error("No browser wallet was found. Install a Polygon-compatible wallet first.");
  }
  const client = createWalletClient({ chain: polygon, transport: custom(window.ethereum) });
  const [address] = await client.requestAddresses();
  if (!address) throw new Error("The wallet did not return an account");
  await ensurePolygonChain(window.ethereum);
  return { address: getAddress(address), client, provider: window.ethereum };
}

export async function signTypedPayload(
  wallet: ConnectedWallet,
  typedData: PolyTradeTypedData,
): Promise<Hex> {
  // The account may hang on a different network since the wallet was attached.
  await ensurePolygonChain(wallet.provider);
  const params = {
    account: wallet.address,
    domain: typedData.domain as TypedDataDomain,
    types: typedData.types as unknown as TypedData,
    primaryType: typedData.primaryType,
    message: typedData.message,
  } as Parameters<WalletClient["signTypedData"]>[0];
  try {
    return await wallet.client.signTypedData(params);
  } catch (caught) {
    if (caught instanceof Error && caught.message.includes("must match the active chainId")) {
      // The chain changed between the check above and the signature request —
      // switch it back and retry once before giving up with a clear message.
      await ensurePolygonChain(wallet.provider);
      return await wallet.client.signTypedData(params);
    }
    throw caught;
  }
}