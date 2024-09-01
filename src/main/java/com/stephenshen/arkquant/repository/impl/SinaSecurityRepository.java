package com.stephenshen.arkquant.repository.impl;

import com.stephenshen.arkquant.client.SinaHqClient;
import com.stephenshen.arkquant.entity.Security;
import com.stephenshen.arkquant.repository.SecurityRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Repository;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 新浪标的数据仓库
 *
 * @author stephenshen
 * @date 2024/9/1 16:39:33
 */
@Repository
public class SinaSecurityRepository implements SecurityRepository {

    @Autowired
    private SinaHqClient sinaHqClient;

    @Override
    public Security getSecurity(String symbol) {
        Map<String, Security> securityMap = getSecurityMap(Collections.singleton(symbol));
        return securityMap.get(symbol);
    }

    @Override
    public Map<String, Security> getSecurityMap(Collection<String> symbols) {
        List<Security> securities = getSecurities(symbols);
        return securities.stream().collect(Collectors.toMap(Security::getSymbol, security -> security));
    }

    @Override
    public List<Security> getSecurities(Collection<String> symbols) {
        // 获取行情数据
        Set<String> sinaSymbols = symbols.stream().map(this::toSinaSymbol).collect(Collectors.toSet());
        Map<String, SinaHqClient.HqData> sinaHqDataMap = sinaHqClient.hqDataMap(sinaSymbols);
        // 构造标的对象
        List<Security> securities = new ArrayList<>();
        sinaHqDataMap.forEach((sinaSymbol, sinaHqData) -> {
            String symbol = fromSinaSymbol(sinaSymbol);
            int lotSize = determineLotSize(symbol);

            Security security = new Security();
            security.setSymbol(symbol);
            security.setName(sinaHqData.getName());
            security.setPrice(sinaHqData.getCurrent());
            security.setLotSize(lotSize);
            securities.add(security);
        });
        return securities;
    }

    /**
     * 转换成新浪标的代码
     * @param symbol
     * @return
     */
    private String toSinaSymbol(String symbol) {
        if (symbol.length() != 6) throw new IllegalArgumentException("Invalid symbol: " + symbol);

        // 沪市转债代码以11开头
        if (symbol.startsWith("11")) {
            return "sh" + symbol;
        }
        // 深市转债代码以12开头
        if (symbol.startsWith("12")) {
            return "sz" + symbol;
        }

        // 沪市股票代码以600、601或603开头
        if (symbol.startsWith("600") || symbol.startsWith("601") || symbol.startsWith("603")) {
            return "sh" + symbol;
        }
        // 深市股票代码以000、001、002或300开头
        if (symbol.startsWith("000") || symbol.startsWith("001") || symbol.startsWith("002") || symbol.startsWith("300")) {
            return "sz" + symbol;
        }

        throw new IllegalArgumentException("Invalid symbol: " + symbol);
    }

    /**
     * 转换成本地标的代码
     * @param sinaSymbol
     * @return
     */
    private String fromSinaSymbol(String sinaSymbol) {
        if (sinaSymbol.length() != 8) throw new IllegalArgumentException("Invalid sina symbol: " + sinaSymbol);
        return sinaSymbol.substring(2);
    }

    /**
     * 确定交易单位
     * @param symbol
     * @return
     */
    private int determineLotSize(String symbol) {
        if (symbol.length() != 6) throw new IllegalArgumentException("Invalid symbol: " + symbol);

        // 沪市转债代码以11开头
        if (symbol.startsWith("11")) {
            return 10;
        }
        // 深市转债代码以12开头
        if (symbol.startsWith("12")) {
            return 10;
        }

        // 沪市股票代码以600、601或603开头
        if (symbol.startsWith("600") || symbol.startsWith("601") || symbol.startsWith("603")) {
            return 100;
        }
        // 深市股票代码以000、001、002或300开头
        if (symbol.startsWith("000") || symbol.startsWith("001") || symbol.startsWith("002") || symbol.startsWith("300")) {
            return 100;
        }

        throw new IllegalArgumentException("Invalid symbol: " + symbol);
    }
}
