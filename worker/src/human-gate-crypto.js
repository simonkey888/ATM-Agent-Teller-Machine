export const CANONICAL_OWNER="0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4";
export const CANONICAL_ORIGIN="https://atm.simondalmasso44.workers.dev";
export const BASE_RPC="https://mainnet.base.org";
export const CHAIN_ID=8453;
const MASK=(1n<<64n)-1n;
const RC=[1n,0x8082n,0x800000000000808an,0x8000000080008000n,0x808bn,0x80000001n,0x8000000080008081n,0x8000000000008009n,0x8an,0x88n,0x80008009n,0x8000000an,0x8000808bn,0x800000000000008bn,0x8000000000008089n,0x8000000000008003n,0x8000000000008002n,0x8000000000000080n,0x800an,0x800000008000000an,0x8000000080008081n,0x8000000000008080n,0x80000001n,0x8000000080008008n];
const ROT=[[0,36,3,41,18],[1,44,10,45,2],[62,6,43,15,61],[28,55,25,21,56],[27,20,39,8,14]];
const utf8=x=>new TextEncoder().encode(String(x));
const hex=b=>"0x"+[...b].map(x=>x.toString(16).padStart(2,"0")).join("");
const unhex=x=>{const s=String(x).replace(/^0x/,"");const b=new Uint8Array(s.length/2);for(let i=0;i<b.length;i++)b[i]=parseInt(s.slice(i*2,i*2+2),16);return b};
const cat=(...p)=>{const b=new Uint8Array(p.reduce((n,x)=>n+x.length,0));let o=0;for(const x of p){b.set(x,o);o+=x.length}return b};
const rol=(x,n)=>n?((x<<BigInt(n))|(x>>(64n-BigInt(n))))&MASK:x&MASK;
function f(s){for(let r=0;r<24;r++){const c=[],d=[],b=new Array(25).fill(0n);for(let x=0;x<5;x++)c[x]=s[x]^s[x+5]^s[x+10]^s[x+15]^s[x+20];for(let x=0;x<5;x++)d[x]=c[(x+4)%5]^rol(c[(x+1)%5],1);for(let x=0;x<5;x++)for(let y=0;y<5;y++)s[x+5*y]=(s[x+5*y]^d[x])&MASK;for(let x=0;x<5;x++)for(let y=0;y<5;y++)b[y+5*((2*x+3*y)%5)]=rol(s[x+5*y],ROT[x][y]);for(let x=0;x<5;x++)for(let y=0;y<5;y++)s[x+5*y]=(b[x+5*y]^((~b[(x+1)%5+5*y])&b[(x+2)%5+5*y]))&MASK;s[0]^=RC[r]}}
export function keccak256Bytes(v){const data=v instanceof Uint8Array?v:utf8(v),rate=136,n=Math.ceil((data.length+1)/rate)*rate,buf=new Uint8Array(n),s=new Array(25).fill(0n);buf.set(data);buf[data.length]^=1;buf[n-1]^=128;for(let o=0;o<n;o+=rate){for(let i=0;i<17;i++){let q=0n;for(let j=0;j<8;j++)q|=BigInt(buf[o+i*8+j])<<(8n*BigInt(j));s[i]^=q}f(s)}const out=new Uint8Array(32);for(let i=0;i<32;i++)out[i]=Number((s[i>>3]>>(8n*BigInt(i&7)))&255n);return out}
export const keccak256Hex=v=>hex(keccak256Bytes(v));
const u256=v=>{let x=BigInt(v);const b=new Uint8Array(32);for(let i=31;i>=0;i--){b[i]=Number(x&255n);x>>=8n}return b};
const hs=v=>keccak256Bytes(utf8(v));
const DTH=keccak256Bytes("EIP712Domain(string name,string version,uint256 chainId,bytes32 salt)");
const ATH=keccak256Bytes("OwnerAction(string requestId,string action,bytes32 nonce,uint256 expiresAt,string origin)");
const SALT=keccak256Hex(CANONICAL_ORIGIN);
export function ownerTypedData({requestId,action,nonce,expiresAt}){if(!/^0x[0-9a-fA-F]{64}$/.test(String(nonce)))throw Error("nonce_invalid");return {types:{EIP712Domain:[{name:"name",type:"string"},{name:"version",type:"string"},{name:"chainId",type:"uint256"},{name:"salt",type:"bytes32"}],OwnerAction:[{name:"requestId",type:"string"},{name:"action",type:"string"},{name:"nonce",type:"bytes32"},{name:"expiresAt",type:"uint256"},{name:"origin",type:"string"}]},primaryType:"OwnerAction",domain:{name:"ATM Human Gate",version:"1",chainId:CHAIN_ID,salt:SALT},message:{requestId:String(requestId),action:String(action),nonce:String(nonce),expiresAt:Number(expiresAt),origin:CANONICAL_ORIGIN}}}
export function eip712Digest(a){const t=ownerTypedData(a),ds=keccak256Bytes(cat(DTH,hs(t.domain.name),hs(t.domain.version),u256(t.domain.chainId),unhex(t.domain.salt))),ms=keccak256Bytes(cat(ATH,hs(t.message.requestId),hs(t.message.action),unhex(t.message.nonce),u256(t.message.expiresAt),hs(t.message.origin)));return keccak256Hex(cat(new Uint8Array([25,1]),ds,ms))}
export function ecrecoverCallData(digest,signature){if(!/^0x[0-9a-fA-F]{64}$/.test(digest)||!/^0x[0-9a-fA-F]{130}$/.test(signature))throw Error("signature_shape_invalid");const s=signature.slice(2),r=s.slice(0,64),ss=s.slice(64,128);let v=parseInt(s.slice(128),16);if(v<27)v+=27;if(v!==27&&v!==28)throw Error("signature_v_invalid");return "0x"+digest.slice(2)+v.toString(16).padStart(64,"0")+r+ss}
export async function recoverOwnerAddress(args,signature,fetchImpl=fetch){const data=ecrecoverCallData(eip712Digest(args),signature),r=await fetchImpl(BASE_RPC,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({jsonrpc:"2.0",id:21,method:"eth_call",params:[{to:"0x0000000000000000000000000000000000000001",data},"latest"]})});if(!r.ok)throw Error(`recover_rpc_${r.status}`);const j=await r.json(),raw=String(j.result||"");if(!/^0x[0-9a-fA-F]{64}$/.test(raw))throw Error("recover_invalid");return "0x"+raw.slice(-40)}
export const sameOwner=a=>String(a||"").toLowerCase()===CANONICAL_OWNER.toLowerCase();
export async function sha256Hex(v){return hex(new Uint8Array(await crypto.subtle.digest("SHA-256",v instanceof Uint8Array?v:utf8(v))))}
