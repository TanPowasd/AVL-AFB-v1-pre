# Vendored from user submission cab6aa76; original hashes in ../references/submission_cab6aa76/manifest.json.
# RSIdemo packaging changes: relative imports and workspace-local native cache.
"""Integer market flow calculus with exact per-unit quotes and price-floor state.

Daily arrival timing is still a scenario. Conditional on that timing, there is
no midpoint-price shortcut and no fictitious inventory added by floor sales.
Finite differences propagate through the actual inventory transition, including
the loss of price impact once a floor sale stops changing inventory.
"""
from functools import lru_cache
from .cache import native_cache
import base64,ctypes,hashlib,os,platform,tempfile,zlib
from .market_model import MarketModel as ScenarioModel,ITEMS
from . import gpt6_rules as rules

LOW=rules.MARKET_I0-5000;HIGH=rules.MARKET_I0+5000
_TABLE={}
_NATIVE_PAYLOADS={'Linux-x86_64': ('88d9c1ea86e4d2e82ecd73623410af655c7ad8f7ed797c57451046b3e65ce834', 'c-rk7ZBSI#^}Pjj17zQtCh1m<ZZa>;?nDV|Na9vgdAKY0$y-E)m{bx(1aSs|;G&p8kS@gBd0AmnC$XJO>W^gFKW&*wlctWH0TB=qQz5A)qmBNE?QSpzuo^#tujjnI4;I9jv@`wFcV^x_@1A?U?m6e)x9?*c{mKdp&vOPOa6jcHdK{O@aQdQn+F~vl+B>;aoi~xYH6$fW#+*HdP%bI1KNa^UPsZ_bx;-bOPr-{VlX1M<3^?*0iS_fAO~&oqWNgS;zNeg(;l$hcIhAb4tvwgq^q<J(F*&EfHwF8+$=Jx*YLJ`3eRnX@w1(OHu5mGH%EHc#d;sj`lI-K-u46C0HZ*hX+FRKd;J6FC@0cCyC&%eWdfUBBk1@fLs;$*km8F%zqAibS#Py0dZYkPcTC;6SRV8PF2_~3ef(a&=V1fxInDBo9o(|G7C^Wx=x1L0`$@HS4uCf#QK5>7fc;DYuMd(oyit;`++oiaV%Cw(^GrF&6nkKskJsm^$3-foQ9^zf)sGmpFA0jhJNL)lh7p#hRASn7<I%?<3-oA#@IMkc%!do@zJ!~0I?P;0LS>GkhhnrKPW;;lzgD2jj_02?GXC?GA#oed4KO|~mKY2KDROc-d<~Qs9ca@c;8Rx?MOpeg2G94lGq~9|LGi%sE)EPv5-cHojPU0QjRpnD9muC=0haS`Uy^^EeE7LC7eUYf1m1@RkGCxW}H~389*F7Y381TE72|p}T@abgmA)L+wx<+qLLnkuyN^w()vDJR6(BoaYe)nNTUF{&=YeZNDPGPuZfgBHnvv?&u*B<b839aQ2PNK@z0R4oxhh_Kh%7mqz<l&{A0s152J}J{{F?A3h=_#3lF9VPksX)pvSWD<f<c|02HWP1uK!wCXoK`uBkUOfl2S})sCy=quFP0I$|Id1!2EY+AK)j>i&j~qv_ZpenWpC%MO0cZZ9zqXC-n#+reEkWbU4(wJ{#mI|dRBT4Lyq}r8EjlnTKa_6l{{v>AN0#YV1V$0gufTkEQCkE43r9LdSh%lO&Ax2_OE}IxJRWzFpAV@7FaE71Ma`X;@BPV9t+S90z&S&K+EUC-roTgiqP*l<vBI9*2p<ovdYm|fOSVdRKlw@#eJEm8KaPVNTKk?;ehu<;~HhjI!B`taE~hL6FCVPCy4NeOT;}Ohq`!~S_ywK;5{8IfW)qrRjZ=zuq)n?U2mZ}J*Nk;@myx53mO}K&(8JXp>|=uF`xd}6r*o_qiLQ~K!-`x=Lmb>hRG@P3z)0o?vLz&QSc!`_@U@8VFdVa6}D<{E`R+g>FtJs&;s<BO#7mjFfQXY4t8`UUqtR2)0K1+zAKuKJhA#%CsPfJ<U^@Irhk+21N4a8d45@e0s-ZZK)GC$2fk6<KlZou1vg5et9&rSGZ=XblzIBVLtJiDlW9-n6(A}6u;M;P+z%VMlZsd!c={sGBLf|QP&1kC$aA_(WT=09eMWMCG<0yhFRhcFVuc$zpH67L=u&-RdFaM{LhF}UQS`b@A@4l_`q!2Yp}7!fe$VHjD?ADNM3+(m6`Cjt&5r_I&2d2?wzPuUas4^!g5Df8wFkXAlcV~&VMoaj73ve6GL6ce(PaeAtuh~#2d*k^1yoW+bVT07l&JZl71=#~AdGzW$jd-iRNzp+O{d~MM%)#mi>MW%UB+#S@0R)VQs~NkjVUpAnqR=h96FM&(6ez-o9tg=k8UDI&7bUF%r|iICjSz3{}krm;JAP1uYX7Xgx1aAn7Y=5+x>Ppjjn^HO^+%mxd1WN6%G}}@*7a^{z_K!zl4OGk=4`-L=B2|66)pybU=Zu>0So-fe9~D#YmCvOsWV=pQw72KgNx~4JJZk;K4a4Q+2tkr6YJC`YD`)(dYn{?nHfG6y{yS8j<M<Sbnieq1Q>%`J-6;>ORqdMWP-Ooq(IJX}In0X@oX({k|P9DBhFxwFI_H_aJGyw)@rnTw@tI$nEZVvmu<oN%R;=*eBx3C9oNW<+0f_VGA3m$s*xbx&B`*A@mZ6ar++IvEvb_Y&rbyFs|=ZT%Vy%EAfs5pCBqvk}|Udjy$=$c^<d@AF+5H#0Q(C=?J<>k55W`?D`0<Tfs0b6f;0|fc6r#S$73CG?`u?^pN;l<fHq<HpXw)*F&?|&glCA_cn{~u}ilCqysD}nR-;3MSKpLr7qYX8nXy{@UjAzXuEzL3Uye?lQ^B=0$H6qlZ1bvPXeL@vk>+y#VdN#QXYxh2cru$+?9N2q!4N@{JQQ`fL>APD5l>DWl;bnu@tKlI#{Cj>m_<f_eD_)MZ5Y(u>+)5(!?{;(=C^UmRS%aU`W@URYW^0Tu|4d)h^Vx<EF#tqukUPL%oRn-oFd2^S~oI%C<|GPq<#JD-*uzph2-*p~HmMi;GG4kciP%N;ZiZNXbOXOM3X~MS^pA8okd`plek?bc2sph!v7B%K}H19UCV$>DXjqlZVX)Y^tI8IjTq8#_$1#+Zpa)_&CF7v4Aq-_HS63t^hw4a9@($-MC8fa_NGza_9(;ojNJ>%{-xbRIh+~SaB3-xG^-xW(ML8?C6uiHI}Pp{BxCBaSj)1uDbLrF4V;V8qpJVHFEwcCM76l>ZGf3_$gjjcP28(M#%Vx78^mEuE^UkT!7}BQaC8`xV_jhX%5^F;CBKP-Aj5&L?Mi>h<FXyL0dWykk^5{PMx<Gd9ijQ8tDAlGf);-!cY*+7a*JZ)iv6w@3WX6qbBkc2!K_^^g^>iFBl*AQxW}W3~OLwztFG3UFL6j6HG9{gzp3RniSvZ;=B0l+yZ<*+krIWbv-{PVF|u7o(ad<uQY7~;Pzon!`-)SMAL9zZ@;K%sjvs<{Y%sG09RkuwATS2fNMe`dk=rNq3?w?9A9VW=cLZGw(+s|*mL2i1Kpka1=3~@Su737HtUL%GMjZ}SIoamI7F~}is`9_BOi`opv^`bSvLD#OODMEPRO!3Urv;4t{0OeTY6}Qr8wE<l5EZ_n*(^VY}V|Q`Y9BDiZ4d-+Z=wzV>vWVGMLZ@{C)TOsHVNf#cZ}(fMIWf-{uS_`faY4lZY+-#TnVQ#i5yX$+mQGBWsdr0%91eE1=^Zi=iV6b!4HAEYu;P4hePmY~_~I$+pG5n^egu+$Ioz8gzXCao68>uNc=wrqLzac3UjJ0cFc?QbtlNAKPBCYz2;lcX&9fvb9-C0ZTy9<KF>x_Fd7mWOK=wV1fxIm|%hlCYWG?2`1=p>4sLN-=DJk-Xump%lgK**85rCxSx8E;S`1)|0931moNW`(^asswlY}j^gNpOXj=Nh$7}WOqJ~8oY3Uhh9yc5KHa)TL@<+6(^gB8H<~wox!|_f=pT+3M8Qr+Azn9V5XT|!nS)Aq?{6C2GUt|0aFnTq6z;KTx{@KRO{ye1nY90Sh-GFqK9pig>1D12se{cQ_cf0S+(@MCFwL7_Or4<$2#^PW}Ik%~JOT|tu2rXCSdnQ*3$jiB+qU_v)B0niwu|^8`i->Q{Exu8o{2e=&wr%IuVDU!4!I~H@H_kR!)~1ygZ!720Htnngwipi9aB0EPx*(TUSrshRnW}55s!MBvJGr#d@}jbu;;p4c<(q0Idb(io)-5Gm8d%>7w*UKjn*+yXO{+4}WULbdyBOUVw@#6NkgXBRlyzia!srRCCaqJ}(?T|8nX)bo{NOZo9S@Ijlm5GK%m1xR9X4PIdzLm{rq0QDO8MP1|6FF@kiXUcJmR14#Y5o~`EuuEe4PJw`(<qI#`D4P|9!CkfwafPKVII5Llv`s(`J%Bk)P?}ACE25)Kxl@ck&baX!11qza!4Kc*yg_@aEg(f7UJJ|HS0$ZX^G1xv6FM'), 'Windows-amd64': ('bb8402df2cdee22c277a85c2def8ec6dd73c7e3db3c5f163a5acafc742166ea1', 'c-rlK3wT?_ndnHeZ21*ABmqMbqJU{_h!fd)U_%0yEjdSUWGj}OI3eX&mgPqy+o~Rk0|{}kQ_u>7q1=|+Wm`9Ncj<0-H|=HHz|yW0LlS70I)RoHXmLwvWm1ai144lK{xfslN3un>Wq0pIpO5F9fBu>OIsg1~&Pd0#Z3Gz*LRoOk%pi0a(N7JU`!fK?yk$?!LyzVD$MVBE_CJ<;ySgKmP`Ix%?CY`keZ9SXQA=CE5{~s+x_d45MvtYZuRXA`urRNL@5AjtsQvTv(Y9~>^Jh^DLP20=8hy%=uh*|cmjlAZaOmN<2#zA$Vtfo=l5;)f8-I)h2{W!kC>x<4EYg8ot#DzX@FIchBGom23c~c_as;6dvSdQK5&Er8c6|sT`z$)KfZ|G1!29n<C^<{JmC-<d6w1-1JVRLaY*pU~wXSUUMSXDIiUC$$Hzi6uU}0=!m?A!8;Ejo8`jiH*W@U)t5gFZle^;l#>!)~k>v-ZAZ$%2cwn&8N6yt@``pwwN0PU|=a3tK{$`t)|Q#@jm5nd<O6nN2KM6~57-`~1adI<#k{Jaeh@px4lyxLW3MDs%{9`Cvocoo&;{O{(&zX{$?*QdZ+T|v(O75|&&`)f!=cP%;pI{B}oc<vN<Yp*5ejG%0c*OUV9TB@u^hy(F>-V}IMl)tmsZdM*|vlb71^7()2V{5i~o40KCFoVA>J#dPN|Hu`8+I-+EP?Ip8<q$eBX1;efxWiylSMg@t_&R_NV~+}SG6}>a`b|t?X=x=wY~rd-`w==*Y+g1%{IdF8^AHCOA5TIAvdz4}#FNvE`CHdB2gaEAvDm9jVgrJ9iJZZ+dh>yHz+{FCd!hT})+GoX&mo=MwfZpjM`rM;(pzrjK4aT%`^<QUc^M4e!E?IUYlM@EH+fz{n3snu2ro|q6K58hmjNHg%tObKhL6?2N9>msZ!(FsyP-)U8w5S@bHF>EjVUJWMTDWB-3sRui?JSJK_;<i7;u?H;Z7)$&ronPfHza{jTrn708R!9q3+G&*?1J0hxT9@62>trE)Y<T7P{ieCvYVbe{W`D(0C8f(M_3WW@dQ(9e6WltQceNGQdB%^xwnWP1OS;)dPt9ICPsZfEr`Vf#Yj1#}@++$8qFGQ~!2;W`=Y$bsIE#d>Iz*OP4_`rR_!QTY$kEtwebY6vv5oV2^k%{=6-|u!M~tkJToOzxoXIi3hRfPbL56QiPoGv+<Xh_}j@tAI{9gpN=PO$FoX+)(kLip^Rgr4Ury)Yi9<}>UPy62LTxr(I~cVg02SN*TtWXeGjzyIKV4TVfb~F1if%Ut22KCc1>PM&<%ioFOiJbTg97Gw^QX%dQALTP|C%?_0)1KHC*Ikwfu@SsX^l-%1_5ip(WMNW>3YK>%f>Z{(O{U5*M)vXAu+s30roIt$uEATf$a~{bnB_G{FqMp<~NVF!ATv;e}711qy80c*U5r`sBV+rg~HHo-5dd4kO)c$SQIUzF}gjUyA*L(s9OD-*W4C$H;M8DYo#Lw}f$&uoW|dV>+h#=)U`zgwy1VPqSsux`0d~>*P(>WF4U`E5^<|iRJM)Dqbf1p1h-#N$8oxrXr?#VsC3=Ybn$?Do!1EIkpjZR>f4?iuY79@o}c?c_uNRNi2NHse7J*fo1;cQRm=Mo%uVb7~O=cdcqmkMHkSXj^1&C=MaEw{PZp3JfCc0lf_wf)Kz`dWp11xOq_C=ndjNW*5aFnmw>qAI=1>{^L-KQ7(|YjnX(CI+4BvCte>((j!RwfaaY;%e7DpJwk@{J@)*U&A$+{Z=AP*9VH20Rs!zD$&$`SF6TqH@EqjR>uGKL|k~cEL_9ERAM1d#IGG^u|3u2fXfxi>Z;jB6)p0hXLDm%%<&$;3!lE-0;Fz=qmx`>~a4>#MbHg25Q2Xwm6<2vxnz6R)mjh}FuU1N+6{`7Ed9xex!xXO;;&Yt~H=<Lk5I!0V&6F@}W=T!4~?0&Rg=#4pk3=bGk-3iv*I7Wk>xp5jx`|fELn2tZ?j30;kUsSw_b^EJ*o2Kr^84;W4E@rFW*teBU%y-6r;w(GXm{|CGN5wl%Fr>}=m1At#@7e0p`@F8|pY3UMmW?wBU4voaY1aHNKVh-CpVl$)OW5kyqYmQCu^X7gCE#>L?C`c3u=n1tKx^LQZ2YBt&tZb#M$p~fZwb!&ZE4iFs(-ZSdh@-Hfqbs`S*+i(6Upy>04g#6%QU0=!_=3F<On;kS}7xd=F-9VIhS1fA!%`p(Tz`aotKSg+h*Pon8covP{Ll)&&1Eeh+~fY?nXv;o;i7*DXWArIW!2!5NV<}uyK1ys{=w{*}F`fEpaEm0e!Ox0(^#n*pmRDGkz?2Kc-oBJ*%7MIkVk5KEgtiE{rI&-a%Ru;F!F%Cq-)<r>-aPccDsxcS!nD`F9nYc&y}6Jo^rn;Ounp+){|ZY<#E$=f1A^56$=eGpM`*LMP*Y$vkAz5h<Briok%Z7W2^U08KQM9(X3U*OgfDbHeG{40Jh4B8s=f;!3Q4#T9>pO%$AV9(m(N21ch#_lEQ2IkxOU(i0p1adH5g4P%4+#F%g6{!5cx*iP{?&ayL1B3M$&mOV^~>z-zGZw<b`bYBi39vx($(cn{6s?kK^1u*b)X81v#YmqruBI^}4{vqazIr1jqi`9L|ocxd}`zFCno_Qab(j8}^>#`(MM&-stA4t@L|3?4rOdQ8SS7OP}iG3AaQ`zG$vGMICrC4w_vHmm_{w0<0gYS#NkGbOSK}XK`)5)IqvGsA|vNM9*&f%aDQOV8s<-#ydY=%_IJhU55r{2TU@66Qih4HSi_x|4|i9sGN=?9D1hHWz&W^lrA*XOZJ?=3aowU?w)50?x;{hkOG1)nixr<n}{UwjDdYh@0jfn#64W7vQg!%zucojD=w4Ld))EC)HOkA@)<-ie<5^7YIH^x)cEyR5F^ho*@q9X(Ts^#p<5<f?u?daV;C3u8^K89~oOm!qA(rt{KalBDc^3NxC@j+iA()gHg6gn<%A{`iqUnC}=W>4Ho1#X}__%v5|L{;au-IyD16-U7f5#@suOF^Ei{1vhR1NMpn)hDs)Q0;4+W+6=zHYx>$m)<cl+*dSNEYugM?aL!!B=R<>ICT0T`F}i|D6d0KOM~G1S-@{Uw?^=PUpZ)8zF>vRpsiiZ#U6|o}2ygayu*Ay5qhR6qa}2nN1^bHo_)y7dK4WG<QhbvE56UVi;}4eL^-{8ujFu-$9wN*hNzfM9S=<N4Zi*MYYCg~c%1qo-GJ=OEZN(;NCv1Xt3PW}!vmYeI65u(L*?(-BSYR9%c5nr(W%GU5p{CmLyajQP=`g<qQ~tYb{0JKdXCdnQ8RP#wuy_x4sQEeO`5TMiu)tx3qXrH)9IbGK%=7OWAXs;e&|;JphiEZIiwRmx;?9RbOhRN&Nf*8t<u5|`;!*yhANv=!SPjw0J*0^<vE*HRK1y`M{)tpPO4RzcOv!lUb+L((X>5;D-VP9HUGd4}d+*T*>~;;?OCSk(8zYA##N=r?!VHhlFCk!8YDs<@u!c9|_Qd9b1z6x5Z2uH1giENj#Jv#tH=To_4JQ;m{<fk8Mt@X5#{^oF%Q60gM5F_!Vvs#<FKJ;6+e;uXqq<<rUSsftj2%0^1m=xny2108n-82gkNKx|Wby2FCPD3(#%ZQ-%0Y65sU~p3juF}XLcr=ISN5S(snK|mxHP$fjE5P>sElvT$F_FEx~j&+#1~ECOGpu3!!y~qkRQ_?7Cmw$?m<6zb|!u#S`E?P5<f9G1vAr<qYTXT!_lK>u|Lc#U<S`X;`@i#pCA>#gRT-_TAcFyk>mvxV>0I9+CE&PiyygZaQ#tG#aL|m%-<^O6UJ?u@QNgwP4l$l#`;a<z6qN0LFXfb=M2&O1E-=nJik+a4J%7o7Xtsr=dXZ}TcwX?A1Ajy2G8lE3m}Dx)>Hn(csIOsYY8saHsPZB57Afob(jGwPY-$<94GR<$u|_@5|MJLl6uET#hB7KGz)<A6?`TBgU9v61;Cp~9>Rbhw$M4aq!2T)=x;0}*dzl5m<5Phq(w3t*WzUrWYyD26I3RzDa32O5t?SW8UNZ2H|XshAY#7v5)yxnEp8mYpMelsEaqEm0<qQOVMzborbLs;shfaH^PPW%Q<uRbu$mbwwN<|zgLL<&$&iWo2NOST9{M$1@*3Tk%_ZMqj=W}Ibb!@wp)1SeAS}ZYTMBGs4bKc06an^&jri1y2M}9aJhSMM&yfzzLwFj5VPYS4e=zvV_oJp^_t@Ys|2=lmpz*8F1h{z04U-ejxA3fR7~k(aoJBZbODuM?eGEr{D#oxh)`;h#pEB{A!J$1xGmE|%B8&L%%q+SsL{|LcutJU>O-9et^<l+3Q_IPU#dsZIu5iaP%(n>=$l~H;0}jB$h1V`4>Q3GWmm^C5Y!)Vbs(6MO4wlp;*1q0=l^>nY3<pd&C*)_<&x{{89&4cfoBSI13iA*%oHYQ)(`IrXWO6$BMlM;Tdn{z}za1B4PaQXYfxnrET?ZyGW1<0j&LmD12G8c)@kRKbZ@%vUo_Y*;%^JaTQC${3?ZT%X6JGfpIH|{E#tWFlhF^eGw(sM030wVIc+ZrZjwh3O=tZ1$o+~mRxE-)<=I=U?ZT}>mCOQs`?apQj@uLK`Y=YIDaA0?EoQ)MT8}QQw^L?Me{ck`6pxt{m!8tp;_beNqO147?vmK`Hehvc$^C%-gJ93*nQD?Fz79?)YvYEd#;nbZQJO?Rk9y`3E+C21Atm)7DfxE$THRc1q!RbcAfhYq!V}yxZ>`U>Nbs|;VfAWnFFlCcOITt8niT?`QKViJV1^M>NppX5%^wtL>Uyp!eEO+7D_q08M9Qe)-jFcjrFeVt&%!?4xfk^4~YtOQfz@3^Z5c0sp0!R@L{0<Q##E<9SZZaPTVQ&JKrna3I?`PN&qH;mEh0NoKJg5Q0+!IO_FJY^v&G&tXd3qe`_Yovmo8J2dlGOa+`&ig4N!TUeO;Zob=$XGG5*(t&oKYL|Vuq%v_mSj%@a%y3p3lo@{BJ@-C(*#lowCw(A1Ky5m`&DD<pWD;BckWqvvA-{)SYE@Zy9o!_<KMp*&t^ZV96d6wavtopANm~5@6xD<1C3O{|+-QSpq>tVhPT2lgWQPNAMQHW%8?VuA2X(`(NzvRrlSD3H@SbQAdyrF79R=e_oERV-n@J6VL{45D#nMH-F~GO#Il)iwq3oLWm%_4EQLl67a-%OA)k+!9wO2(k(D%=BiR?%M(i&^KZr$jsJ*=|0I@2Zt`Ko@h6J$y-G6v@6X}G>3DQC6AzhS&iUO<5S*$ol||z_i1R&p6J0;Wmwua^)j>=fgQ_#%Jd7RS%-<`*Q4K%eC5-pnM9BXjHcz<0IL_z6YvZ)bnMJLHMB!F^4HNpc6{pTDR&;0Q8GJUn7-zh?>eKknLF^0z6Yk|qytXL$<7~dK70{QA5&(@l==aReRQwU`z~pNl`ZhpzS)nI3Vf?w1485W6;~TWbA3AA-6z&aMZjtUU!h9e5O~Uw3q(Ss&$tB<>XMQ5lNEja=Rk4nQ@opzsJjcEZrj9zPsm1%(FiC(NlMAy6JoXm&Pzd83H#(d&(jGU~@n<uO%0CM(9t4FaZ&osv{A+y2@(WVD7%nVx;uZ^V3+s%=AyuE3UsL7>kRR=@c5RE98ULpBjPB(AYWxH>Y7+7jT)(?GUvGYJY@FoZejA`2v75hZ%Z6iN2Oh-3c3p9_(l*S%$S8=j!|aJtfj@hq214#pSV@FU1_lzDXT$k;l4q+=#9o7BMYs>g#DA0=$s)twv}+zX;{Ul6!vFp%*S6?Fh?dfAG$H@-cL5(y@83(F_{Lu8*uNLJjJ*Jp$HI=mz1g6c?Z)EhQx)&<_oYch#<$KOu~?NwtoaQ<&gLU(^|A0PXTI1m0-0y^rBv+eKrKbAzji$&DYpGpO1*U0_V;6gZ^3j`vydq}=^VDBVh3DgW0geL*oNJpjfD?@HYQ3Xf1&z-Wz|v82V}z4C&S;A>jNZz9ENPgGemZ57B|Q_!IRI8pWx;1;K`f6%VblakY5>h7Zd(%v;!w}!0L|fzfQ&f{@ZZ-X)t6h7_wF{q`o-1LSP+Ir-@P?|DBpoTcWvm*nW?}X*jUVR3D8cfn`Ph3H-lz3BHvt^3Py@;oT~iiBF94{9~_T%=QTkl6nCBKYqza(Z2(00Q3)baAP5!aSnTnilZ;$yB|2$0sS9U=)acge+=|r3;I72E?@?amO86X68$G?OWvJ0l3XU;hhAEC2}xst;`Nv#m%nbkOLtn}Xda8zFKK}1Ys!`w7$K8v^yk2$J6quI6y@(3$8haTBk<QS>{ar26YzJ!6+aQqwF84}^%x|}guzp>UjiGL9G>@dr_bc!{FK;YA-Gi*7%-NTa2G&`d-1?C`(C#vm;#sXr?`PM-Z!}+TO!Y**plM#rOf^*as}BlQ%=rE?XKT2Wk)cuWvgnfr(XZj;R^}k5Ab7*`0?cJh-eGe3FL0339sBO$^W1M%!;QZMSrw?4Ks`v`+O(%k`P{=^y8*g$*WU#xQBLFqwbIyHa>2UaY*7nzwk-kNCM`f;zbY-AfmuJXD`jgKM#<{aSk$5q>7g_SA_$x(2<olsmgQhwA2SOE1w|vg1u+1dVp^)lwrPX<I5(#tmVr}zPy4jFB0hU<?sFS{-<tvT`?v6rJw)mRM$0i{QesLGsAy`YrP6zwe<vztWdW9<n!PBvuqA~jnaA(U$P-uhUzl2v#Yzeb65GUb!+e@uxNKrz|!IC4#vWP>+<OHkM{O(AQG`$6|!{sB9`7hOLvd2Gf*B0_@mu@y~twO+#Qa_e8EkzKzKK49B8+6^o1=~wOiVDM*|UyFKY3Ly@-3Igm&(5UljThTJGwOc3J#=?E#Cm|EhkB*WA0Kw{KUkB@_w7+WRbIe~=H;#v9q9eg3|nWoIB9!GNpUSLTWRr*GHR9koQdZwturF}DELA_MmzdtWSS>FcnBeZ8H56_%(k+!=_1=AdeYWp{TV*p4rvT>;BZUoZw8hlGv=tp>tjqLVz8{;gJ}oh%x=&0ep;uTfr~y<d6l4k@ooRP?Q?`YP2mqoU_lUAL&NTUFN^>*}tvl=fGx<JPSy5A{X5`@xmE%EN)36)P;YvF>0Lyv71vxp7N5zHBG^6+a6eQf8@GyRvenYHyhk|3=DZkBc95yiH#I-y}{#_Hb@Kzo+jagkFSWA)HS_`4$`{a6S#?2XK^PoCPpvz_9|(El~at4lA5bK>5%@uqT|4LizU>gR8*#At+;zkhQ@10F*l*S?_}L5R}iu`hNh^g7VjJJk(0}JzfXL!*E^$<+tJZI-HL|`6?WTFg}!jg5xoazYL)W9EWlLP!7WJ6r7Jh`8zmHVE&<OT8_{QaNY{#?_tuK#BHwvKHxapO3PosVe$b!l>0vgdVup0DDV6<$O`A9P(A`jDV!gI@)bB%!1*MU)>1fpwER9CYv6ng%9=9B5q-4W1qTD?15o|}4%SEaTCRh`jd7rS1`aQrPeA#_Yd~+fe<*(q$9Bvclvl0*IWVmi_$e1cLEI;lcf-+-d4}>R9DDG2IYR#njv-%~`M)3s71WxnIjz|tqdRL%KLN13Y?N1-Z8f%Lg$!=}n2ygA$f4IGy;aw$)9H{7#)_^e7ZueOSo2%+Lb-0!SoTELq<$LUxki**YqVyy8bbO>9<MD0-hw=|K(`dBYY43@9m=YOGTVT%YcW+BA8HuBZsV_WvQW+g2CKeR7t$@tb1X6x-O2YqmNQ|TG)(K<8X0k$O1_P}Z=)U=D|J?d4opU5s?D+*TJ<5FTLw>c;C`F<e(`yjKS#>G|0_Ra?={}GIzV~W^G7LMTj8i0pkow|*_+_Jb%17${=S}2UvHo{y0SeOl&&~mTQ|o~nKEeix;39<TmS7o@)d^ZLAp<UQU<dC>;%Bh%3yYYO#;kxzXTfs*fd--GT0%2Ssws@k-<&_jCmkEKdpRSKVOdU<tSf1$(Jwi<tu#o248-l>bu|zw7mEWO8Dif>r(!@hA$buZ0E}mU*5r&Uskn0$X`FEf<MP!pZ>!CpK_M!?15k)8mJ3*N4x#Lpoh=v8+4A|XdtWxqqu%!Aj<k8Q3p<F5&DwB*$XJXAmr0(<UbkMfN!S;jQ+vs2?hco^qkQfj6ipran6j+8<i^p{9OY1+5(|V5%fa79dH@y2nS&Oe`8ippvNDAx+@VE=nwc~(EtbGtiL<TMSX3-0IYvGe1+B_u3KbOiGr$V9f;e5s-<%-fNnrsIMCT0flHxHN355Mu?6|s`od9&vmEF1harIWwUJts1&~Ozy}OTt=-SiQ%kAt4g}ZyB9q3UMiH3s#(7`|AvtEBsi2qK@T*u9hrUnPQx{`!)_$(ZuhcDD!-V-U`)!kbTJd}3@e4+ASMR`TJm134^ae$YdzGy&!hSrayKUD4cC5^JZdxRb;T%ra!W;@`tr@{jTbt=33<nzhrlMm$Z1$Y@a>K?^k@F8D?8nuj;j#@{nMkl{E{WWy3=%D4G^<d3G_rcbKp@RblM-Gl2JalmEVCf^FNBST6M1}v8KP$w0x+a=`Ttn|mRHngSLhwsP{7*jrvp@L$y%AQl;<EsS1K?=zC0sLb+kA?*5Uz4IHZ)u6Dp&J2%q-<S(O7R_gTF5vsDuk&*x$9Gx6j|#>$8-13V)52($Ue|SKi$Yi$XYU!#Cl`+}z#P>G#vhNYv-wQ68d+8l-_s4I3{upm%s~wqqaEW&68)VWc<Z1p9hBE#zOUH`3kN8)&ycG6Xe7sfGabIgu{Ns8ncKM!t{`vvQOuMOmb-y)TCIIlay#R`Wb$2~}BH9l<_d6q!tGL7TbIQwqlPM#=}iFm&U4l~{j}7q)l%0}=FF5bqk@56z(V7O3+!H#j&?9pkV!vkp1TZENy6Z7kR1*vQ!(ZbyUN(NMP)>13@uX|eR_vFhDiU1P&$N0ZkhZ(&H^A{Ba;gl>0uyj;DF_25%CSKrvgZDi|^QQ4u#%{slfI}gg%3R%zEHWFGMuZ?9<j)dWA+}wgpvTKK{v1u#ka(Y}gZym!o$dwT4n}J%L%gxyu>|AY=tpSi$SE9VxG1jd?`Lknq8Zna|RG>n$vCT3xRH&*3{z)~Up?R}5#iAkeoG6^r<w8ZXqcu6*piS21b#8W``4X<JuCCeD3~&e6*zDcd=xo@?*_uG8i)1EgYGfNXHaoax53UCO9cY16?QlCa)eEI+%na+W0dSG5%VrP58xCD8a|`HzrM2i1nLjl)*9zcbEq0?DU8+R}=w))ZvU47$u?hULxuMSMY-~V_m9<R{Z*x<F2Q85)vaz{|yQvwh&N^LAFIp<o8|&QIz<He;8862+Ho86Nav5Wd_1-PECagI}y#ss_>!sGw;HY=jfp4Q_5-MBg<{j1|0h=7akE_w{tlx^3D>dnCsIxoj&=nHY+2D0-bTo1HdT2qs%i%#+O7)FyFK64>j7nt8Z*Fwju~Ep-wAt-V=qefaY~vPisb&`Y)#^&(gr7=NU4=fa?iM>_qZ>RA_gzQ4p;TitkGBa<z(i$oHv?rgaW^+KZfO7&xUndnT0m{|)S|0p<K5{Y+IQHAuzX+GOGQ`oKs;5b&0}Ng(F!H3j`iA6xe`|EMJv?;DFa!p%ZXOaj^f=`*Wg9gIr?Ees6vKfzo{95W^)6KH5OIM%!f@}Pa-au+S7nmYZP7&@z;UY$VOy~n*^9z4_d2~k8u(wsds|v*Qrz_tGZTMRbSuaaJW&GoPw9DvaYVW7BdF^+u-$}YN>i7_<9qaD?EIxcY;{gDJOvD2EmSOBRD)bs?!Cd<N9=1;0a9*5pBJCdaAn_<52&?ni4TLC^5ZT-452_@zDM}=mupq=Ecg@Zspu9jZNrA<v?o!c3{#XgN*Ywa+_f+*35!Z-_+<LNVb$nbRP9UH1eQY85?9~R17599F6Lf)`9N5Dhzu{46)jw8qIDG(_CMR>eZgLrgp2Bj!S0?C~Bj!*NrwW1A<iI8QX&xl{eMdyk4o+nX>5`T6t6IrhM(qDVtW&N;Y*<zSgA{sG_o}-eu$J7#lbUYEV~Ku3aaqZd6yVt*De$yQS*QmG*k@b$c_JScE{vKwx#-(I%yPfnU_s(OAPm>a-a($;q?;3+cv%XBI@|(C}(7Ww}<fq7pPBHruReCe&=v)KHCpV{vXrlMA&d5I|9Kid!|UN!-Rm%;~5D$J(alm&hYw+@k4U)x~x#1F9}QqrvcQk<S>nYEp<TPA`LJN5skJ{VlewFs>n~f|L_{k%J(JT4ioXA~>Fuz`V#OGd|<2_g0`b39`+Br>qt)xB*YA!H)cjyvx<-6@xaOb5Xl2JF&rpK?f%aq)Z;TElp0Z19d2JXZTmaZFbr?h;E%yO+zD|ecU!@lMQ4?U8$EHG*#)=R`N+WAx@D#s}*Du5d`f>B_ef|pj5?6jdhHA)UlCm<TFhV>Q#n-mO5r*E$UMO0n>^?N?~bGBn*8{J<|Dr37HnI4olU1HVs<0JLzDD+z!@3Ufa!J?a17Sm@-9<O30gJqQ+9GjVfoSDr|eab!eAT(5AX3)Gx~}!9~z+e$9!O>54VyuXUaxetl_c0%mctro47zZSo6DIjE|ZUr!QX3mzi|>AKdl#pdSMg9Jd>FybY=VijFv<ba<jgdjN25zfW6Gpw7lxO%+0u2=^PZ3BL?91QeZB$!Er(M7nvNCXJ$bBhR3tmun`o-u8Ux@!RD;Ri2VC<<&|U9<|8_E|HatHtvX%16L9aSzrB1CT?jitw!xj<d^PRtzIeJaAI*5V_fsjhpW&sDuBx2J|_4+a-GyT<+z8)1klkCL!H?RCLLF-9=`@yv6gbnzz7+VWr4mT9S{Dz69mx-;i%GA!NwX8B8}U&&gk&<y>&xa^3aD>gCyZBT$x3=UjkKjBB9Pb<6eF=T|SE?_6->rRy&Ran_)XfNsF(dCmp;VuW($>#i~_$RE+=->=IyEL*&+=n{lXW}xJRUUC<qPeZ%Bi*aZ9&=-N`FUOBKmm}wbRr#OXv-@*?TFCuB6Zz>iolf`uyh2>?yI0_38Sy`k+;Ll`H|y`kHRK=4)eH9u4Sz(sE3W9=YK`68*=gOtUU7w92aWA~-|N%$ZMYIn?u6rSb$tI>Cvg@2ik|+L`0RH&?H1)vK7Yo~LVhnJ*sI@CkYgIvNzVmRJrA(WE4ou}n^!zw=$%(IW}9a^`iTzzCx7a$TSe}`hx_`Xt0H0ls!qSZoZi=8MeoS3^7r+2ba$@mUR||ryMK4*uI}EIu;{#QyV!0!zumTCZKYMV=Qc(j=0~}m6|2eqTiRf|L_lb_3yvPVPkeRdc1jcBCk!@^rE=w(%1><cm$m<2)Q%s`z0k+yUKy}Oi^`QM<ji=X4dlIQd0CY)FSDYkRVeX|FIRS<&bDAVeux%<bE#7JtnBE<Ta?Ii!1FT*mVh8idoU<NAs=D;unt*ZrcUikJK(@yU&cStN`8D0;V+kk^z_O=9x_Tzg~WCgj)6IFj`5|`^Yd_w&CzNK??Idc2XEjXodahPfJWxPxduSiIdGcz&9y{_bk`j~+;g-V0#NHLIKC*^fQBZ;o`v5M7M|%EbZ_bL4mSBm_#I)P7lTe~RN=RLMa}D$XmR?bj*J9H`|HQTZw%v47Qw;ZmaD|!{cyG9hr2uRNWt5;+5(*d-ai7H)B4%{kZf2tsI@^`Ve0ddI;j-2Q6y>urPYQ-)CNkcjZ#q?rnK595w#(V8Lc)NC2fd%t%ZTv7TZmC=<<>(`JulK90|~!ZWAeS=r0$y`J(<VJPNW>;apGs)Yl!v)DC5*!r4InM*#<y139U1ZWPb(kB5YEQ(4d^p1lt6$-?wj7Hmpo$2vTgr@{F%+te!BG?><=>qVR5pIBa`!FwP2pAg4#owgs|?tank5UzWCFeNI(G5KBDdFk~N;=jfu%1*9|;k0I+JpNn_1^%i<y4b>Tm^h3mMXQZ^nZ~4OGf%L^t9Xyd9JVlN$Dqa*S!r$YuxN|ow6++@AlmE@_1x==Mxws3G`vK)CPleY_y#YRMIl#eAG3;b6{NNM{USfnU?dU?sb^u2Uf?Hv^lKx(IV`u==Wwf8t=!+v;oo*Q@HiB58|YAk;LqKFJYZJGRY^aO@K(|4$C|+ytP%B7l-BN#iFRL@*6xqZX7{=#Z%GSl?>D5@h76}JG#gG>n$ai@qm`ZXW8XHS=m(|3vE4l<4!dn&ZX8?JY&hUjCEK=bGx1tUha;S)#o>MRYtr0=ceDrk1KP1OVxD{KWa1m{+2S*iy>6R(PW{x`m^pEr+gj(wv9-*G!|Uf8qJBui&vgZYp#Wsze_8!xrRC=-kstac<Z|*QWXWa^>IHr*DQV%)BY3_La0mqdP(!;o7eu=PTqNL+bSZN6rvayRzC0qms;6H#m2lnwNYNab@M5U6%z=X~jAp^nYQ7DI?KKyy`PSK@79S;A#VB!$E?s0hAx1W9+Q@b~gBk6#d<Kbjh1*qAh~GdHli931XrQYy$vXEN=u0{z$XB5VEt1>xNr2p~!1)n=mx}`DEr5(%tXZcl5^QlNpj~mnwh%ca(Kxl9&t{<K4@5m<oPv~ko|Nmk$1hJDq<6Vo@=->2R$93xXN!J3KW~ZrK#1ZxLKRktA-D_&B7rF7Cw1n)qaYC{g%pOgQq*K%oyknrO*+9kS!u1aLMK?KK=d@VHr^GrL1yTzRBMMr8Cd6|$)!(gokE=$djt5FE!274C0PPL>GQg)0cY|O`Dm&Jh_zS_X#mKW0<r@jHJ8dEg8&&&K)#6ebD13Sces@TG7gZ5%hbLh>G0PEO+@+#I-JhHH&*E89=TPbZ{RiB;*_?yQnW>fZ%05&?{(f=3dFk^*&3X;d7Q#DxhT1_#N*5(INP=Pp)kG{3p%X)Px;|&I>euRp8R}*^QDwHi+P-UQO{bRzM97=5_5j-XDyFo7NfB~r7hNqwn%YjQdnOs{_~7eNk5j9JY&0;igv$aemc7^6z%@Cv^unkI<%lv4=}NBY!`il(X5vRdwof?mrs{2w$zC_Y!3!i_X_O6pbIbHsSanqmm=vo`#+`UBHi3$d3y$N{_7dU`Awqi_zt}IJy?=s3m4NF$UZH~zFO<6e`eWRMcE6|%6=#V**T@`g6MvJHYeW)wl0}lc8Y^(+5h6Q2Q!fU$qZ!g)yd~~&ZzFI79az&$le#iU$Krza*5g)5Vc`SdzZK-gS$PMXrn)aQE(yyZQQH$1Yb|2lYS{($mfB`$dcK!i+cg5CX;p368UVEl~O<M4DRyHsvmycG$!7&x+tv<_vq%n)BeJ2I^^Y=5adGgH!SkLq2?LUw-T0IJCQ}d2Ps0<GI|wc4Ib|b$hzkYB7~=Y`huvxprP*8Q18-EKP^$`{paCXGNP_tUk?UJQglSSV!im6f%R%BCYQ`TQ@8$SBMFrP|EMCRPO>jUDjcom<<{Y4KRk=<{{?cb2?;!>@@HO(7X6$!*Pa%9L$^HrjDJC1_o&9}tQxOlG+uX5<MYW2qW(<{^(qZ@w}$#-8lOLwfzLA<pSNm!e)?ndd3OdruhG2RI=t*g{oFqPG0L^3B?F(Y$-w6ii9T;hpXptY*L7*UZd$G9H71SMm1=zc>;+K|YN(&pP%qL@ulX}k4{4|)&4?@3P-isM(b5Z{9@S7cX{cK?)Y~=GO&3JHUqih}L%mc(J*1&tbV1bjXs8!!s9QDE@6b>$z98xY8tN7e^(qbZFKZ(00{z^P)})EFvhgK6>tFiuMcVZlL|V;wm&XKN_R<U@?Z+tBnwktEZD|IPc1Vo0SES#YaY0^Jqw%^)O{_bM)LwVF#_QH);B`_zg4a!Iyw3VDdR=J-UZ;^=t{-0ZrVPC9W0Y$R%E0TAbK_iV&%o=f7vy!t8m~K{@w!Qk*A-|YZPf))U!tKtp`o7CP`_B?^Q{^9{G`U`PiTC;=415vg&Fv~M)Pv(@Us6?20s5W%C%-1&F%B2Gw^wf==00c`h3j=d0m#q>kezYZbIXAA81xU%mq<L8tR8M)K6%rzoDV-z98x!7}V4s)leVPP=7^3z2$<apVd$w)lfgIq5gt~dg}#I|D%TbLmKLbG}NEeQ17}R>eCwPBO2<DYN(HDsE00y`foMV@77Qs)leVNQ18DW>PZdt0S)zsG}QYw)CVqz`mZ$9`!&=@G}K!)cMtw8gIQ)oGt2aAW|@(Xah5?D%rY9;<@(`ee<p)j=3|s=&FKtgnXwFJ8Iw566sFz7@UlL|&ip`cyMXr%qab_gokbs~e(>jeMma5G%IB~@Fw*Z=q<ad3KQ9^Sx***X1pJu-oYGnLi*j7QAMd9X_9)V)Z0{K94t5E$D4XtZGl`Jp01>Q1Ase#kPB{tEm@Pf0p^#evQl;p*CmV^s+DIV}XG^&sg?uAhxJM-H`Z$^`?FFTfliAX@b@W)SAl^CLrJyyPEyZUF`7m28>HHk}OaZ?^gF?!3=%xY*Qk$cet0jl-5SMVmIn;U*<lY=LKchKP{NZ^9uk9CcxM(oKfhU_3l0KOu?Tx39Q#n$u#qVPTNQ;8j+d0znFAC9{)cjmzl517?g=x!Dx#pLeq`i*7A>P#G@`}5~C{CS8dS*u<TTD_$L?M2jR<;QFoJnnq2Tf|te-j{83LkqGPVZKD%g^xJeic2x3y=u~jy_lVeiMZ(&Xsn0Q^=ZJDTn0c0?0!O4n4Wj?sN(XU>uPa{iST-PM4s=fm~@%DTVxlq92ZH4+c4o?#K{je}dn}Btd=zkkg7WIFl>o02Hz?Px`JIg<P2@?YgIs)c`rG(9ex|YRxz2(QgJxR_+0aU8FAjLb+8T=~wdTcMv36kLIa0_Cy|CAxhMb<w<uuDCGG(xo^;4QJ1n@-ox{x_)H<^^VA+ykWcr?NIh5azuO=|n)0O>PDW)nj*zX2es0Z|zFR>dJM+~e?(_N5cMK`y>i`*0$o204VHIBYY`*aPOVHs<`SkZrC7ieO>3(9`v+#TBrxmRX1!~zZDv-VjNFkL4^t(4Q*$e2dX$dk=AUvB8cjXmGJDGX!1;~)X4__{jp2Jc|l5Zt>)cXJ#QD|dvp_IK-$l5|0wIrOjLOSC~{p>2#L{YHUfNV5D2<1nUv?KdKp?W6zLLoghNp|<CZTfA%8CCH7{X(^#UoDhYKzcgE`sDV)pFbAToobR4h4a+oV%a=;CE=6<<Wc1~ohRLqrjT3a33v4cd&TBy;_5uQlSig87`Y(@&Nt=>&nAR^{&gPRTPMv#N9WP!)Dra{<5mj0zYY+)!h6qS>I!cuH&e?<)Exk^C~zWX;Yp3a&t5Z);Sx^5EIkROkgu85G4pW^<XN+{*O5YAG)v!#qL4Su(sx~Xdljj*x~zzvNj+a*q^5;`$Y?|_8@c422os9&)m9{ZgP5Ngi=?#`h1^v{ztbm4`W+2Fr;DU-5L3u+iqt;#Zjtc38&jw6mJse13jCPoOW)(rBg=f84F}p{ot%%n6)qxNcK3GliB)ZF;lNJeD%jl{5Y9WocqPVPfCA(=<eH=u&IYF!95WQ|gTWJx`utsic7FR!$;xn`BNPp<<hWj6w0kFR<qJn+Ar9uB9djZ<84d8?P3GT&p=m-Qek!Oh1=ODs*PjZveRnTO>G0Q*I6Pst`G8}7FC>WVp*(6E(l6@xLLn}?I}}JOBfrm*3kQ6WzFyE@r{6EV$AfB~0^0*Bu<~_Csb87IlfCV>+i-@`$8`q#+I*0*;_vV)Amm&9S}x^3MkT$Iq#a3$!CfJxo;2O?87BuAAOqhb90)<8#gUf{q8?v(2gkpsk=qsSrdICp_46-c0cJx1{38S$|3(jvf9)Z(-Q}ZXS6a3FH#IkSoh}FG_BfjDjreDZ8|$b%_?PjxkJ2CovJ<~vvep;DCL;EZc7-v6+>XF*s^gU*{5?>syxt!{LN$z_NEGV0UF+6J2)_1qu>)m8s$DgHIMCAvo}_NVd0CI>u=1~Im1C+a{fOHP#srB1GBN7Ad)wU(;q5od%i7%y)EjB{8v{|kqP{Q8zj`x8WnCW_Ox2U4KLF$b$L)~Oc19ZddIj4%d+TE1Ft{^85c>`E#(KE!9^yH{?zXTmyqm-Ki9TXP6C+pZuqdk$Mj&>!ApfQux*?VmHhz+glCxo8T}vA&HC_bbaI{J}z{!}Tw>nlvgfK(4DoU3j<t6=Xj&*C-u3n4XWHx7}bm?M;!@2~Qg8w@L;od-SbtUHmRZ`Ijf3g=1!C(iXG&+-x6hBYLbp?FwQh&ZsH`f#4c0q{4YU1DL!v!n23eE}vmv+yuMAE+Yh~r3b5lGH4Qz+j01c3&~Mu_M0J@AV){7Zp8KL`an?}j=oN`#Q^;K)k@$SaY$sf^V9divU9!2s7C!5wJO@wcABzzxCoeHO-dB-R$$4HoSY0y5PX)<l0$ra`QO)bF-R9f}q2ni4wfR&^-zISGa4)si+*rlC*kt@0OQaa>y@B6o?o0<WYy{2C~^rU`*G@1&x&D*IB?VAlyoBkke7DA&;!_6wd%5~Q@#vZ{3?*2gziz28evV7s6|{yL=6dCHfFWj-BA-3nxYJ@BKxh{A!=jV}6TDsgb}BRJ*D(KI=dda9P}%!v~-jLgp79-L!fF4AQ%nT~xSz+hjDMMzR1C|Odf)8-X*fm!13aLpClNQ4sF)ny!>h%ZX9Nr@|kM&8|p{&_|0^!tOLWl0fK8b6OWdk##cOKB=TuWAqUYu?_LCICvLWg}9qg}L%u-u!9bCpWtmRc@H(ZFi~DsC0b_X`kA82&4R>jN|a*1(lcL`t+~;OJ6gWh&VjSW-%J?RpS&puVewmEGf;ZspmUfP4AgOLkDZy$~j$b{Ei8`vx)qI@K&+slrcvYU*%I1^^K^EGijO<I8(-UJ{F`f;w&zbj&1p9R!3PymLc3qS7Qsao+2;N%md)p!ZeVX5k-$mHzYC&8bU8JzIYEMH~fU@O+AdM`;gbwD<gfMuN~)uL0@NtH(u9{NOT9#N9h*RbFDDt`4E2QCQLG^lNDO0=1j9}R;<HQr!upTDD?nGnJnHED|*SZ1*b`uM5vO0{|nE-H1Y')}
_NATIVE_HANDLE=None
_NATIVE_PRICE_BUFFERS={}
NATIVE_STATS={'calls':0,'accepted':0,'range_fallback':0,'load_failure':0}


def _load_native():
    global _NATIVE_HANDLE
    if _NATIVE_HANDLE is False:return None
    if _NATIVE_HANDLE is not None:return _NATIVE_HANDLE
    key=platform.system()+'-'+platform.machine().lower()
    if key not in _NATIVE_PAYLOADS:
        _NATIVE_HANDLE=False;return None
    try:
        expected,packed=_NATIVE_PAYLOADS[key]
        payload=zlib.decompress(base64.b85decode(packed))
        if hashlib.sha256(payload).hexdigest()!=expected:raise ValueError('native payload hash mismatch')
        suffix='.dll' if os.name=='nt' else '.so'
        path=os.path.join(str(native_cache()),'farm_economy_'+expected+suffix)
        def verified():
            try:
                with open(path,'rb') as stream:return hashlib.sha256(stream.read()).hexdigest()==expected
            except OSError:return False
        if not verified():
            fd,temporary=tempfile.mkstemp(prefix='farm_economy_write_',suffix=suffix,dir=native_cache())
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(payload)
                try:os.replace(temporary,path)
                except OSError:
                    # Another process can have installed and loaded the same
                    # DLL before us. Windows may lock that complete target.
                    if not verified():raise
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
        if not verified():raise ValueError('native cache hash mismatch')
        library=ctypes.CDLL(path)
        library.component_abi_version.restype=ctypes.c_int
        if library.component_abi_version()!=1:raise ValueError('native ABI mismatch')
        i64=ctypes.c_int64;p64=ctypes.POINTER(i64);p32=ctypes.POINTER(ctypes.c_int32)
        function=library.component_i64
        function.argtypes=[ctypes.c_int,i64,p64,ctypes.c_int,p64,p64,p32,p64,p64,i64,i64,i64,ctypes.c_int,p64,p64]
        function.restype=ctypes.c_int
        _NATIVE_HANDLE=(library,function)
        return _NATIVE_HANDLE
    except Exception:
        NATIVE_STATS['load_failure']+=1;_NATIVE_HANDLE=False;return None


def _native_prices(item):
    if item not in _NATIVE_PRICE_BUFFERS:
        prices,prefix,floor=table(item)
        if max(prices)>1000000000 or min(prices)<0:return None
        i64=ctypes.c_int64
        _NATIVE_PRICE_BUFFERS[item]=((i64*len(prices))(*prices),(i64*len(prefix))(*prefix),floor)
    return _NATIVE_PRICE_BUFFERS[item]

def table(item):
    if item not in _TABLE:
        prices=[rules.market_price(item,i) for i in range(LOW,HIGH+1)]
        prefix=[0]
        for p in prices:prefix.append(prefix[-1]+p)
        # The logarithmic wheat/egg curve reaches its floor far beyond the
        # cached range. A cache boundary must never become an economic floor.
        lo=int(rules.MARKET_PARAMS[item]['I0']);span=1
        while rules.market_price(item,lo+span)>1:span*=2
        hi=lo+span
        while lo+1<hi:
            mid=(lo+hi)//2
            if rules.market_price(item,mid)==1:hi=mid
            else:lo=mid
        floor=hi
        _TABLE[item]=(prices,prefix,floor)
    return _TABLE[item]

def sell(item,inv,n):
    n=int(n)
    if n<=0:return 0,inv
    prices,prefix,floor=table(item)
    step=max(0,min(n,floor-inv))
    if inv>=LOW and inv+step<=HIGH+1:
        revenue=prefix[inv+step-LOW]-prefix[inv-LOW]+n-step
        return revenue,inv+step
    revenue=0
    for _ in range(n):
        p=rules.market_price(item,inv);revenue+=p;inv+=p>1
    return revenue,inv

def buy(item,inv,n):
    n=int(n)
    if n<=0:return 0,inv
    prices,prefix,_=table(item)
    if inv-n>=LOW and inv<=HIGH+1:return prefix[inv-LOW]-prefix[inv-n-LOW],inv-n
    cost=sum(rules.market_price(item,inv-j-1) for j in range(n))
    return cost,inv-n

def batch(item,inv,own,rival):
    """Return cash difference and ending inventory for same-item lockstep orders."""
    own=int(round(own));rival=int(round(rival));inv=int(round(inv))
    return _batch_integer(item,inv,own,rival)

@lru_cache(maxsize=131072)
def _batch_integer(item,inv,own,rival):
    if own>=0 and rival>=0:
        paired=min(own,rival)
        floor=table(item)[2]
        changing=min(paired,max(0,(floor-inv+1)//2))
        inv+=2*changing
        if own>=rival:
            value,inv=sell(item,inv,own-paired)
        else:
            value,inv=sell(item,inv,rival-paired);value=-value
        return value,inv
    if own<=0 and rival<=0:
        paired=min(-own,-rival);inv-=2*paired
        if own<rival:
            value,inv=buy(item,inv,rival-own);value=-value
        else:value,inv=buy(item,inv,own-rival)
        return value,inv
    sign=1 if own>0 else -1
    sold=own if own>0 else rival;bought=-rival if own>0 else -own
    value=0
    for _ in range(min(sold,bought)):
        ps=rules.market_price(item,inv);pb=rules.market_price(item,inv-1)
        value+=sign*(ps+pb);inv+=(ps>1)-1
    if sold>bought:
        extra,inv=sell(item,inv,sold-bought);value+=sign*extra
    elif bought>sold:
        extra,inv=buy(item,inv,bought-sold);value+=sign*extra
    return value,inv

def daily(item,inv,own,other,consumption,phase=0):
    # The three explicit arrival scenarios cover interleaved, own-earlier and
    # opponent-earlier deliveries. They are assumptions, not hidden timestamps.
    own=int(round(own));other=int(round(other))
    return _daily_integer(item,int(round(inv)),own,other,int(consumption),phase)

@lru_cache(maxsize=131072)
def _daily_integer(item,inv,own,other,consumption,phase=0):
    first=consumption//2;inv-=first
    if phase==0:value,inv=_batch_integer(item,inv,own,other)
    elif phase==1:
        a,inv=_batch_integer(item,inv,own,0);b,inv=_batch_integer(item,inv,0,other);value=a+b
    else:
        b,inv=_batch_integer(item,inv,0,other);a,inv=_batch_integer(item,inv,own,0);value=a+b
    return value,inv-(consumption-first)

class MarketModel(ScenarioModel):
    daily_value=staticmethod(_daily_integer)
    def __init__(self,obs):
        super().__init__(obs)
        self._parts={};self._marginals={}
        # Existing own private stock is observable and must be in today's book.
        self.initial={k:int(obs['private']['shed'].get(k,0)) for k in ITEMS}
        for inv in obs['private']['inventories']:
            for k,n in inv.items():
                if k in self.initial:self.initial[k]+=n
        # These rounded scenario trajectories never change during this book.
        # Preserve the exact integer conversion and scenario order once.
        self._scenario_inputs={k:[(index%3,
            tuple(int(round(v)) for v in rival[k]),
            tuple(int(demands[d][k]) for d in range(30)))
            for index,(demands,rival) in enumerate(self.scenarios)] for k in ITEMS}
        self._native_enabled=bool(_load_native())
        self._native_inputs={}
        if self._native_enabled:
            i64=ctypes.c_int64;i32=ctypes.c_int32
            for item,scenarios in self._scenario_inputs.items():
                rivals=[v for _,rival,_ in scenarios for v in rival]
                demands=[v for _,_,demand in scenarios for v in demand]
                phases=[phase for phase,_,_ in scenarios]
                if max(map(abs,rivals),default=0)>1000000 or min(demands,default=0)<0 or max(demands,default=0)>1000000:continue
                self._native_inputs[item]=((i64*len(rivals))(*rivals),(i64*len(demands))(*demands),(i32*len(phases))(*phases))

    def _native_component(self,item,own,want_gradient):
        if not self._native_enabled or item not in self._native_inputs:return None
        inv=int(self.obs['market']['inventory'][item])
        if abs(inv)>1000000000 or max(map(abs,own),default=0)>1000000:return None
        buffers=_native_prices(item)
        if buffers is None:return None
        prices,prefix,floor=buffers
        count=len(self.scenarios);i64=ctypes.c_int64
        own_array=(i64*30)(*own);values=(i64*count)();twice_grad=(i64*(count*30))()
        rivals,demands,phases=self._native_inputs[item]
        NATIVE_STATS['calls']+=1
        status=_NATIVE_HANDLE[1](self.day,inv,own_array,count,rivals,demands,phases,
            prices,prefix,LOW,HIGH,floor,int(want_gradient),values,twice_grad)
        if status:
            NATIVE_STATS['range_fallback']+=1;return None
        NATIVE_STATS['accepted']+=1
        gradients=[[.5*twice_grad[s*30+d] for d in range(30)] for s in range(count)] if want_gradient else []
        return list(values),gradients

    def component(self,item,flows,want_gradient=False):
        key=item,tuple(flows[self.day:])
        if key in self._parts and (not want_gradient or key in self._marginals):
            return self._parts[key],self._marginals.get(key)
        values=[];gradients=[]
        own=[int(round(v)) for v in flows];own[self.day]+=self.initial[item]
        native=self._native_component(item,own,want_gradient)
        if native is not None:
            values,gradients=native
            self._parts[key]=values
            if want_gradient:self._marginals[key]=gradients
            return values,gradients
        for phase,rival_amounts,consumption in self._scenario_inputs[item]:
            @lru_cache(None)
            def tail(day,inv):
                if day>=30:return 0
                gain,nxt=self.daily_value(item,inv,own[day],rival_amounts[day],consumption[day],phase)
                return gain+tail(day+1,nxt)
            inv=int(self.obs['market']['inventory'][item]);values.append(tail(self.day,inv))
            g=[0.0]*30
            if want_gradient:
                for day in range(self.day,30):
                    value,nxt=self.daily_value(item,inv,own[day],rival_amounts[day],consumption[day],phase)
                    vp,np=self.daily_value(item,inv,own[day]+1,rival_amounts[day],consumption[day],phase)
                    vm,nm=self.daily_value(item,inv,own[day]-1,rival_amounts[day],consumption[day],phase)
                    g[day]=.5*(vp+tail(day+1,np)-vm-tail(day+1,nm))
                    inv=nxt
                gradients.append(g)
        self._parts[key]=values
        if want_gradient:self._marginals[key]=gradients
        return values,gradients

    def score(self,own_flows,risk=.25):
        scores=[0.0]*len(self.scenarios)
        for k in ITEMS:
            values,_=self.component(k,own_flows[k])
            for i,v in enumerate(values):scores[i]+=v
        scores.sort()
        return (1-risk)*sum(scores)/len(scores)+risk*scores[1]

    def marginal(self,own_flows,risk=.25):
        # A discrete derivative can correctly be negative: adding output can
        # damage our larger later inventory more than it earns now.
        out={k:[0.0]*30 for k in ITEMS};components={};total=[0.0]*len(self.scenarios)
        for k in ITEMS:
            values,gradients=self.component(k,own_flows[k],True);components[k]=gradients
            for i,v in enumerate(values):total[i]+=v
        tail_index=sorted(range(len(total)),key=total.__getitem__)[1]
        for k in ITEMS:
            gradients=components[k]
            for d in range(self.day,30):
                v=[g[d] for g in gradients]
                out[k][d]=(1-risk)*sum(v)/len(v)+risk*v[tail_index]
        return out
