#include <bits/stdc++.h>
using namespace std;
constexpr int C=1000000000, MOD=1000000007;
using R=pair<int,int>;
// Sorted maximal runs of 1 bits. Negative bit positions are supported in flow.
struct B {
 vector<R> v;
 B()=default;
 explicit B(int x){v.emplace_back(x,x+1);}
 B(int x,int y){if(x<y)v.emplace_back(x,y);}
 bool empty()const{return v.empty();}
 void put(int l,int r){if(l>=r)return;if(!v.empty()&&v.back().second==l)v.back().second=r;else v.emplace_back(l,r);}
 int cmp(const B& b,int sh=0)const{
  int i=(int)v.size()-1,j=(int)b.v.size()-1;
  for(;i>=0&&j>=0;--i,--j){
   auto [l,r]=v[i];auto [s,t]=b.v[j];s+=sh;t+=sh;
   if(r!=t)return r<t?-1:1;
   if(l!=s)return l>s?-1:1;
  }
  return i>=0?1:j>=0?-1:0;
 }
 void apply(const B& b,bool sub){
  if(b.empty())return;
  if(empty()&&!sub){*this=b;return;}
  if(sub&&!cmp(b)){v.clear();return;}
  B o;
  o.v.reserve(max(v.size(),b.v.size())+1);
  int i=0,j=0,a=0,d=0,c=0;
  auto next=[](const vector<R>& z,int i,int on){return i<(int)z.size()?(on?z[i].second:z[i].first):INT_MAX;};
  int p=min(next(v,i,a),next(b.v,j,d));
  while(p<INT_MAX){
   if(next(v,i,a)==p){if(a)++i;a^=1;}
   if(next(b.v,j,d)==p){if(d)++j;d^=1;}
   int q=min(next(v,i,a),next(b.v,j,d));
   if(q==INT_MAX){if(c&&!sub)o.put(p,p+1);break;}
   if(!sub){
    int z=a+d;
    if(z==1){if(!c)o.put(p,q);}
    else if(z==0){if(c)o.put(p,p+1);c=0;}
    else {o.put(p+!c,q);c=1;}
   }else{
    int z=a-d;
    if(z==0){if(c)o.put(p,q);}
    else if(z==1){o.put(p+c,q);c=0;}
    else {if(!c)o.put(p,p+1);c=1;}
   }
   p=q;
  }
  v.swap(o.v);
 }
 static B terms(vector<int>& a){
  sort(a.begin(),a.end()); B b;
  int i=0,p=0,c=0;
  while(i<(int)a.size()||c){
   if(!c)p=a[i];
   while(i<(int)a.size()&&a[i]==p){++c;++i;}
   if(c&1)b.put(p,p+1);
   c>>=1;++p;
  }
  return b;
 }
};
struct Flow{
 struct E{int t;B b;};
 int n,s,t; vector<E> es;vector<vector<int>> g;vector<int> lev,it,que;
 Flow(int v):n(v+2),s(v),t(v+1),g(n),lev(n),it(n),que(n){}
 void arc(int u,int v,B w){if(u==v||w.empty())return;g[u].push_back((int)es.size());es.push_back({v,move(w)});g[v].push_back((int)es.size());es.push_back({u,{}});}
 bool bfs(){
  fill(lev.begin(),lev.end(),-1);lev[s]=0;que[0]=s;int q=1;
  for(int k=0;k<q;++k){int u=que[k];if(u==t)continue;for(int id:g[u]){const auto& e=es[id];if(lev[e.t]==-1&&!e.b.empty()){lev[e.t]=lev[u]+1;que[q++]=e.t;}}}
  return lev[t]>=0;
 }
 B dfs(int u,const B& w){
  if(u==t)return w;
  for(int& i=it[u];i<(int)g[u].size();++i){
   int id=g[u][i],v=es[id].t;
   if(lev[v]!=lev[u]+1||es[id].b.empty())continue;
   B f=dfs(v,w.cmp(es[id].b)<0?w:es[id].b);
   if(!f.empty()){es[id].b.apply(f,true);es[id^1].b.apply(f,false);return f;}
  }
  lev[u]=-1;return {};
 }
 void go(const B& inf){while(bfs()){fill(it.begin(),it.end(),0);while(!dfs(s,inf).empty()){} }}
};
struct Hist {int u,m,t;};
int n,M,c,T,H;
vector<int> goal,first;
vector<Hist> hist;
struct Diff{int hi,lo,k;};
struct Bounds{
 vector<int> low,high,order;
 vector<Diff> cs, cost;
 int val(int id,const vector<int>& x)const{return id>=0?x[id]:(id==INT_MIN?0:hist[~id].m);}
 bool constrain(int hi,int lo,int k){
  if(hi>=0&&lo>=0)cs.push_back({hi,lo,k});
  else if(hi>=0)low[hi]=max(low[hi],hist[~lo].m+k);
  else if(lo>=0)high[lo]=min(high[lo],hist[~hi].m-k);
  else return hist[~hi].m>=hist[~lo].m+k;
  return true;
 }
 explicit Bounds(int k):low(k,1),high(k,C){}
 bool init(vector<int>& x){
  int k=(int)low.size(); x=low;
  vector<vector<pair<int,int>>> in(k);
  for(auto a:cs)in[a.hi].emplace_back(a.lo,a.k);
  for(int u:order){for(auto [v,d]:in[u])x[u]=max(x[u],x[v]+d);if(x[u]>high[u])return false;}
  return true;
 }
 bool move_step(vector<int>& x,int h,int sign){
  int N=(int)x.size(),mx=-1;
  vector<int>d(cost.size());vector<char> blocked(N);
  bool any=false;
  for(int i=0;i<N;++i){blocked[i]=(sign>0?high[i]-x[i]:x[i]-low[i])<h;if(!blocked[i])any=true;}
  if(!any)return false;
  for(int i=0;i<(int)cost.size();++i){
   auto a=cost[i]; d[i]=val(a.hi,x)-val(a.lo,x)+a.k;
   if(a.hi>=0||a.lo>=0)mx=max(mx,d[i]);
  }
  if(mx<0)return false;
  B inf(mx+20);
  Flow f(N);
  f.es.reserve((cost.size()*3+cs.size()+N)*2);
  for(int i=0;i<N;++i)if(blocked[i])f.arc(i,f.t,inf);
  for(auto a:cs)if(x[a.hi]-x[a.lo]-a.k<h)f.arc(sign>0?a.lo:a.hi,sign>0?a.hi:a.lo,inf);
  for(int i=0;i<(int)cost.size();++i){
   auto a=cost[i];int u=sign>0?a.hi:a.lo,v=sign>0?a.lo:a.hi,e=d[i];
   if(u>=0&&v>=0){
    f.arc(u,v,B(e-h,e)); f.arc(u,f.t,B(e-h));f.arc(f.s,v,B(e-h));
   }else if(u>=0)f.arc(u,f.t,B(e));
   else if(v>=0)f.arc(f.s,v,B(e-h));
  }
  f.go(inf);
  vector<int> plus,minus;
  auto on=[&](int i){return i>=0&&f.lev[i]>=0;};
  for(int i=0;i<(int)cost.size();++i){
   auto a=cost[i];int z=sign*(int(on(a.hi))-int(on(a.lo)));
   if(z>0)plus.push_back(d[i]);
   if(z<0)minus.push_back(d[i]);
  }
  B p=B::terms(plus),m=B::terms(minus);
  if(p.cmp(m,-h)>=0)return false;
  int lim=C/h;
  for(int i=0;i<N;++i)if(on(i))lim=min(lim,(sign>0?high[i]-x[i]:x[i]-low[i])/h);
  for(auto a:cs)if(sign*(int(on(a.hi))-int(on(a.lo)))<0)
   lim=min(lim,(x[a.hi]-x[a.lo]-a.k)/h);
  int a=1,b=lim;
  while(a<b){
   int mid=(a+b+1)/2;
   // The mid-th step improves iff plus*2^(2*(mid-1)*h) < minus*2^-h.
   if(p.cmp(m,int(-(2LL*mid-1)*h))<0)a=mid;else b=mid-1;
  }
  for(int i=0;i<N;++i)if(on(i))x[i]+=sign*a*h;
  return true;
 }
 vector<int> exponents(const vector<int>& x)const{
  vector<int>a; a.reserve(cost.size());
  for(auto t:cost)a.push_back(val(t.hi,x)-val(t.lo,x)+t.k);
  return a;
 }
 void core(vector<int>& x){
  if(x.size()==1){
   if(high[0]==x[0])return;
   vector<int> plus,minus;
   for(auto a:cost){
    int e=val(a.hi,x)-val(a.lo,x)+a.k;
    if(a.hi==0&&a.lo!=0)plus.push_back(e);
    if(a.lo==0&&a.hi!=0)minus.push_back(e);
   }
   B p=B::terms(plus),m=B::terms(minus);
   int l=0,r=high[0]-x[0];
   while(l<r){
    int mid=(l+r+1)/2;
    if(p.cmp(m,1-2*mid)<0)l=mid;else r=mid-1;
   }
   x[0]+=l;return;
  }
  int room=0;
  for(int i=0;i<(int)x.size();++i)room=max(room,high[i]-low[i]);
  for(int h=room?1<<(31-__builtin_clz((unsigned)room)):0;h;h>>=1)
   while(move_step(x,h,1)||move_step(x,h,-1)){}
 }
 void optimize(vector<int>& x) {
  int N=int(x.size());
  if(N<=1){if(N)core(x);return;}
  low=x;  // Componentwise least feasible score vector.
  vector<vector<pair<int,int>>> into(N);
  for(auto a:cs)into[a.hi].emplace_back(a.lo,a.k);
  for(auto it=order.rbegin();it!=order.rend();++it)
   for(auto [v,d]:into[*it])high[v]=min(high[v],high[*it]-d);
  vector<int> par(N),si(N,1);
  iota(par.begin(),par.end(),0);
  auto rt=[&](int v){while(par[v]!=v){par[v]=par[par[v]];v=par[v];}return v;};
  auto join=[&](int u,int v){
   u=rt(u);v=rt(v);if(u==v)return;
   if(si[u]<si[v])swap(u,v);
   par[v]=u;si[u]+=si[v];
  };
  for(auto a:cs)if(low[a.hi]-high[a.lo]<a.k)join(a.hi,a.lo);
  for(auto a:cost)if(a.hi>=0&&a.lo>=0&&low[a.hi]!=high[a.hi]&&low[a.lo]!=high[a.lo])join(a.hi,a.lo);
  vector<int> id(N),idx(N);
  vector<vector<int>> members;
  for(int i=0;i<N;++i){
   if(low[i]==high[i])continue;
   if(rt(i)==i){id[i]=int(members.size());members.emplace_back();}
  }
  for(int i=0;i<N;++i)if(low[i]!=high[i]){
   int j=id[rt(i)];idx[i]=members[j].size();members[j].push_back(i);
  }
  vector<Bounds> work;work.reserve(members.size());
  for(auto& a:members){
   Bounds b(a.size());
   for(int j=0;j<(int)a.size();++j){b.low[j]=low[a[j]];b.high[j]=high[a[j]];}
   work.push_back(move(b));
  }
  for(auto a:cs)if(low[a.hi]-high[a.lo]<a.k){
   work[id[rt(a.hi)]].cs.push_back({idx[a.hi],idx[a.lo],a.k});
  }
  for(auto a:cost){
   if(a.hi>=0&&low[a.hi]==high[a.hi]){a.k+=low[a.hi];a.hi=INT_MIN;}
   if(a.lo>=0&&low[a.lo]==high[a.lo]){a.k-=low[a.lo];a.lo=INT_MIN;}
   if(a.hi<0&&a.lo<0)continue;
   int r=id[rt(a.hi>=0?a.hi:a.lo)];
   if(a.hi>=0)a.hi=idx[a.hi];
   if(a.lo>=0)a.lo=idx[a.lo];
   work[r].cost.push_back(a);
  }
  for(int i=0;i<(int)work.size();++i){
   auto& b=work[i];
   if(b.cost.empty())continue;
   vector<int> y=b.low;b.core(y);
   for(int j=0;j<(int)y.size();++j)x[members[i][j]]=y[j];
  }
 }
};
long long modpow(int e){
 long long b=2,a=1;
 for(;e;e>>=1,b=b*b%MOD)if(e&1)a=a*b%MOD;
 return a;
}
int gettime(int id){return id>=0?T+id+1:hist[~id].t;}
Diff edge(int hi,int lo){return {hi,lo,abs(gettime(hi)-gettime(lo))};}
int strict(int hi,int lo){return gettime(hi)>=gettime(lo);}
struct BIT{
 vector<int> f;int n;
 BIT(int n):f(n+1),n(n){}
 void add(int i){for(;i<=n;i+=i&-i)++f[i];}
 int sum(int i){int s=0;for(;i;i-=i&-i)s+=f[i];return s;}
 int kth(int k){int x=0;for(int h=1<<(31-__builtin_clz((unsigned)n));h;h>>=1)if(x+h<=n&&f[x+h]<k){k-=f[x+h];x+=h;}return x+1;}
};
int fixedshape(){
 int L;cin>>L; if(!L)return 0;
 vector<int> who(L),p(L),rec(M+L+1,INT_MAX),best(n,INT_MAX);
 for(int i=0;i<L;++i){cin>>who[i]>>p[i];--who[i];rec[p[i]]=i;}
 BIT tr(M+L);
 for(int r=1,j=0;r<=M+L;++r)if(rec[r]==INT_MAX){
  rec[r]=~j;best[hist[j].u]=min(best[hist[j].u],r);tr.add(r);++j;
 }
 Bounds bd(L);
 for(int r=M+L;r>=1;--r)if(rec[r]>=0)bd.order.push_back(rec[r]);
 for(int r=1;r<M+L;++r)if(rec[r]>=0||rec[r+1]>=0)
  bd.constrain(rec[r],rec[r+1],strict(rec[r],rec[r+1]));
 for(int i=0;i<L;++i){
  int u=who[i],a=best[u],b=tr.kth(goal[u]);
  bd.cost.push_back(edge(rec[b],rec[a]));tr.add(p[i]);best[u]=min(best[u],p[i]);
 }
 vector<int>x; if(!bd.init(x))return 0;
 bd.optimize(x);
 long long ans=0;for(int e:bd.exponents(x))ans+=modpow(e);
 return int(ans%MOD);
}
struct General {
 struct Node{int gap,j;}; // number of historical rows preceding the new row
 int maxgoal, depth=0;
 vector<Node> board;
 vector<int> low,high,best;
 vector<Diff> cost;
 vector<int> lower,answer;
 vector<int> initialSlack,users,gstart,gnext,glow,ghigh;
 B incumbent;
 bool found=false,cut=false;
 uint64_t visits=0;
 clock_t deadline;
 General():maxgoal(0),low(H),high(H),
            best(n),initialSlack(n),gstart(M+1),gnext(M+1),glow(M+1),ghigh(M+1) {
  vector<char> hot(M);
  for(int i=0;i<n;++i){
   best[i]=~first[i];initialSlack[i]=goal[i]-first[i]-1;
   if(initialSlack[i]>=H&&initialSlack[i]>=0)continue;
   users.push_back(i);maxgoal=max(maxgoal,goal[i]);hot[first[i]]=1;
   for(int j=max(0,goal[i]-H-1);j<min(M,goal[i]+1);++j)hot[j]=1;
  }
  sort(users.begin(),users.end(),[&](int u,int v){return initialSlack[u]<initialSlack[v];});
  for(int i=0,j=0;i<=M;++i){gstart[i]=j;if(i<M&&hot[i])j=i+1;}
  int j=M;
  for(int i=M;i>=0;--i){
   if(i<M&&hot[i])j=i;
   glow[i]=j<M?hist[j].m+1:1;
   ghigh[i]=gstart[i]?hist[gstart[i]-1].m:C;
   gnext[i]=j+1;
  }
  deadline=clock()+clock_t((H<=5?.91:1.85)*CLOCKS_PER_SEC);
 }
 int rank(int id,const vector<int>& pos) const {
  if(id>=0)return pos[id];
  int x=~id,f=0;while(f<(int)board.size()&&board[f].gap<=x)++f;
  return x+1+f;
 }
 int at(int p)const{
  int i=0;for(;i<(int)board.size();++i){
   int r=board[i].gap+i+1;
   if(p==r)return board[i].j;
   if(p<r)break;
  }
  return ~(p-i-1);
 }
 int lo(int id)const{return id<0?hist[~id].m:low[id];}
 int hi(int id)const{return id<0?hist[~id].m:high[id];}
 bool cannot_beat(){
  if(!found)return false;
  vector<int> a=lower;return B::terms(a).cmp(incumbent)>=0;
 }
 void finish(int r,int tight){
  Bounds bd(depth);
  for(const Node a:board){bd.low[a.j]=low[a.j];bd.high[a.j]=high[a.j];}
  for(int i=(int)board.size()-1;i>=0;--i){
   bd.order.push_back(board[i].j);
   if(i>0&&board[i-1].gap==board[i].gap){
    int h=board[i-1].j,l=board[i].j;
    bd.constrain(h,l,strict(h,l));
   }
  }
  if(r>0){
   int a=at(r);
   if(tight>0){if(!bd.constrain(at(tight),a,1))return;}
   else if(a>=0)bd.high[a]=min(bd.high[a],C-1);
   else if(hist[~a].m==C)return;
  }
  vector<int>x;
  if(!bd.init(x))return;
  bd.cost=cost;
  bd.optimize(x);
  vector<int> ex=bd.exponents(x);
  B b=B::terms(ex);
  if(!found||b.cmp(incumbent)<0){
   found=true;incumbent=move(b);answer=move(ex);
  }
 }
 bool mandatory_bound(const vector<pair<int,int>>& bad,const vector<int>& tight,int forced,int rem){
  if(!found)return false;
  vector<int> ex=lower;
  auto add=[&](int a,int q,bool alreadyBad){
   int am=hi(a),atime=gettime(a),e=INT_MAX;
   for(int p=q-!alreadyBad;p>=max(1,q-rem+1);--p){
    int b=at(p);
    e=min(e,max(strict(b,a),lo(b)-am)+abs(gettime(b)-atime));
   }
   if(rem>1)e=min(e,max(1,lo(at(q))-am)+T+depth+1-atime);
   ex.push_back(e);
  };
  for(auto [u,r]:bad)add(best[u],goal[u],true);
  for(int i=(int)tight.size()-forced;i<(int)tight.size();++i)add(at(tight[i]),tight[i],false);
  return B::terms(ex).cmp(incumbent)>=0;
 }
 bool candidate_tick(){
  if(H>2&&(++visits&127)==0&&clock()>deadline){cut=true;return false;}
  return !cut;
 }
 void dfs(){
  if(cannot_beat()||cut)return;
  vector<int> pos(depth);
  for(int i=0;i<depth;++i)pos[board[i].j]=board[i].gap+i+1;
  vector<pair<int,int>> bad;
  vector<int> tight;
  const int rem=H-depth;
  // Users originally safe by at least H ranks cannot be displaced past their target.
  for(int i:users){
   int slack=initialSlack[i];
   if(slack>depth)break;
   int r=rank(best[i],pos);
   if(r>goal[i])bad.emplace_back(i,r);
   else if(r==goal[i])tight.push_back(r);
  }
  sort(tight.begin(),tight.end());
  if(bad.empty()){finish(0,0);return;}
  if((int)bad.size()>rem)return;
  int earliest=M+1;
  for(auto [u,r]:bad)earliest=min(earliest,goal[u]);
  int forced=int(tight.end()-lower_bound(tight.begin(),tight.end(),earliest));
  if((int)bad.size()+forced>rem||mandatory_bound(bad,tight,forced,rem))return;
  if(bad.size()==1){
   const auto[u,a]=bad[0];
   int r=goal[u],q=tight.empty()?0:tight.back();
   if(q<r){
    Diff d=edge(at(r),best[u]);
    cost.push_back(d);lower.push_back(max(strict(d.hi,d.lo),lo(d.hi)-hi(d.lo))+d.k);
    if(!cannot_beat())finish(r,q);
    cost.pop_back();lower.pop_back();
   }
   if(rem==1)return;
  }
  struct Choice{int u,a,e;Diff d;};
  vector<Choice> choices;
  for(auto [u,a]:bad){
   Diff d=edge(at(goal[u]),best[u]);
   choices.push_back({u,a,max(strict(d.hi,d.lo),lo(d.hi)-hi(d.lo))+d.k,d});
  }
  sort(choices.begin(),choices.end(),[](auto a,auto b){return a.e<b.e;});
  for(auto [u,a,e,d]:choices){
   if(mandatory_bound(bad,tight,forced,rem))break;
   cost.push_back(d);lower.push_back(e);
   if(!cannot_beat()){
    int ori=best[u],r=goal[u],nt=(int)tight.size(),nb=(int)bad.size();
    for(int pass=0;pass<2&&!cut;++pass){
     int allow=rem-1-nb+(pass==0);
     if(allow<forced)continue;
     int from=max(pass==0?1:r+1,nt>allow?tight[nt-allow-1]+1:1);
     int to=pass==0?r:max(maxgoal,a);
     to=min(to,M+depth+1);
     // Only slots with a genuine score gap are usable, checking old and new neighbors.
     for(int p=(pass==0?to:from);p>=from&&p<=to;p+=(pass==0?-1:1)){
      if(!candidate_tick())break;
      int idx=0;while(idx<depth&&board[idx].gap+idx+1<p)++idx;
      int g=p-idx-1,j=depth;
      if(gstart[g]!=g){
       if(pass==0)p+=gstart[g]-g+1;
       else p+=gnext[g]-g-1;
       continue;
      }
      int l=glow[g],h=ghigh[g];
      if(idx<depth&&board[idx].gap==g)l=max(l,low[board[idx].j]+1);
      if(idx>0&&board[idx-1].gap==g)h=min(h,high[board[idx-1].j]);
      if(l>h)continue;
      int lsave[11],hsave[11];
      copy_n(low.begin(),depth,lsave);
      copy_n(high.begin(),depth,hsave);
      low[j]=l;high[j]=h;
      board.insert(board.begin()+idx,{g,j});
      bool ok=true;
      for(int b=idx-1;b>=0&&board[b].gap==g;--b){
       int u=board[b].j,v=board[b+1].j;
       low[u]=max(low[u],low[v]+strict(u,v));
       if(low[u]>high[u]){ok=false;break;}
      }
      for(int b=idx+1;ok&&b<=depth&&board[b].gap==g;++b){
       int u=board[b-1].j,v=board[b].j;
       high[v]=min(high[v],high[u]-strict(u,v));
       if(low[v]>high[v]){ok=false;break;}
      }
      if(ok){
       if(p<=a)best[u]=j;
       ++depth;dfs();--depth;
       best[u]=ori;
      }
      copy_n(lsave,depth,low.begin());
      copy_n(hsave,depth,high.begin());
      board.erase(board.begin()+idx);
      if(cannot_beat()||cut)break;
     }
    }
   }
   cost.pop_back();lower.pop_back();
   if(cut)break;
  }
 }
 int go(){
  dfs();long long ans=0;for(int x:answer)ans+=modpow(x);return int(ans%MOD);
 }
};
int main(){
 ios::sync_with_stdio(false);cin.tie(nullptr);
 if(!(cin>>n>>M>>c))return 0;
 goal.resize(n);for(int& x:goal)cin>>x;
 hist.resize(M);for(auto& a:hist){cin>>a.u>>a.m>>a.t;--a.u;T=max(T,a.t);}
 sort(hist.begin(),hist.end(),[](const auto&a,const auto&b){if(a.m!=b.m)return a.m>b.m;return a.t<b.t;});
 first.resize(n,M);
 for(int i=0;i<M;++i)first[hist[i].u]=min(first[hist[i].u],i);
 H=C-T;
 cout<<(c?fixedshape():General().go())<<'\n';
}
