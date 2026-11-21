#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <fstream>
#include <vector>
#include <limits>
int main(int argc,char**argv){
 if(argc!=5)return 2;
 int channels=std::atoi(argv[3]);if(channels<1||channels>6)return 2;std::ifstream in(argv[1],std::ios::binary),cf(argv[2],std::ios::binary);
 int16_t co[16];cf.read((char*)co,sizeof(co));if(!cf)return 3;
 std::vector<int16_t> pcm;int16_t v;while(in.read((char*)&v,2))pcm.push_back(v);
 if(pcm.empty()||pcm.size()%channels)return 4;
 size_t samples=pcm.size()/channels,frames=(samples+13)/14;
 std::ofstream out(argv[4],std::ios::binary);
 for(int ch=0;ch<channels;++ch){int h1=0,h2=0;
 for(size_t f=0;f<frames;++f){std::array<int,14> x{};for(int j=0;j<14;++j)if(f*14+j<samples)x[j]=pcm[(f*14+j)*channels+ch];
 int predictor=0;int64_t peakBest=std::numeric_limits<int64_t>::max();
 for(int p=0;p<8;++p){int64_t peak=0;int a=h1,b=h2;for(int s:x){peak=std::max(peak,std::abs(int64_t(s)*2048-int64_t(co[p*2])*a-int64_t(co[p*2+1])*b));b=a;a=s;}if(peak<peakBest){peakBest=peak;predictor=p;}}
 int scale0=0;while(scale0<12&&(peakBest>>11>>scale0)>7)++scale0;
 int bestScale=scale0,bh1=0,bh2=0;uint64_t bestError=UINT64_MAX;std::array<int,14> bestCodes{};
 for(int scale=scale0;scale<=std::min(scale0+1,12);++scale){int a=h1,b=h2;uint64_t err=0;std::array<int,14>codes{};
 for(int j=0;j<14;++j){int64_t pred=int64_t(co[predictor*2])*a+int64_t(co[predictor*2+1])*b;double q=double(int64_t(x[j])*2048-pred)/double(2048<<scale);int code=std::clamp((int)std::round(q),-8,7);int decoded=std::clamp(int((int64_t(code)*(2048LL<<scale)+pred+1024)>>11),-32768,32767);int64_t delta=decoded-x[j];err+=delta*delta;codes[j]=code&15;b=a;a=decoded;}
 if(err<bestError){bestError=err;bestScale=scale;bestCodes=codes;bh1=a;bh2=b;}}
 h1=bh1;h2=bh2;out.put(char(predictor*16+bestScale));for(int j=0;j<14;j+=2)out.put(char(bestCodes[j]*16+bestCodes[j+1]));
 }
 }
 return out?0:5;
}
