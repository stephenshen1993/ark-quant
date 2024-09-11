package com.stephenshen.arkquant.strategy.impl;

import com.alibaba.fastjson.JSON;
import com.google.common.collect.Sets;
import com.stephenshen.arkquant.entity.Account;
import com.stephenshen.arkquant.entity.Position;
import com.stephenshen.arkquant.entity.Security;
import com.stephenshen.arkquant.entity.TradingPlan;
import com.stephenshen.arkquant.repository.SecurityRepository;
import com.stephenshen.arkquant.strategy.TradingStrategy;
import lombok.Data;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 标的池的交易策略
 *
 * @author stephenshen
 * @date 2024/8/29 07:10:23
 */
@Component
public class SecurityPoolStrategy implements TradingStrategy<SecurityPoolStrategy.Context> {

    @Autowired
    private SecurityRepository securityRepository;

    @Override
    public List<TradingPlan> makeTradingPlan(Context context) {
        Double balance = context.getBalance();
        Map<String, Integer> holdingQuantityMap = context.getHoldingQuantityMap();
        Set<String> targetSymbols = context.getTargetSymbols();

        // 获取标的信息
        Set<String> symbols = Sets.union(holdingQuantityMap.keySet(), targetSymbols);
        Map<String, Security> securityMap = securityRepository.getSecurityMap(symbols);
        System.out.println("securityMap => " + JSON.toJSONString(securityMap));

        List<Security> holdingSecurities = holdingQuantityMap.keySet().stream().map(securityMap::get).toList();
        System.out.println("holdingSecurities => " + JSON.toJSONString(holdingSecurities));

        List<Security> targetSecurities = targetSymbols.stream().map(securityMap::get).toList();
        System.out.println("targetSecurities => " + JSON.toJSONString(targetSecurities));

        // 计算总目标价值
        double totalValue = balance;
        for (String symbol : holdingQuantityMap.keySet()) {
            Integer holdingQuantity = holdingQuantityMap.get(symbol);
            Security security = securityMap.get(symbol);
            totalValue += holdingQuantity * security.getPrice();
        }
        System.out.println("totalValue => " + totalValue);

        // 分配目标价值到每个股票
        Map<String, Double> targetValueMap = new HashMap<>();
        for (String symbol : targetSymbols) {
            targetValueMap.put(symbol, totalValue / targetSymbols.size());
        }
        System.out.println("targetValueMap => " + JSON.toJSONString(targetValueMap));

        // 分配资金到每个股票
        Map<String, Integer> targetQuantityMap = new HashMap<>();
        for (String symbol : targetSymbols) {
            Double targetValue = targetValueMap.get(symbol);
            Security security = securityMap.get(symbol);

            Double price = security.getPrice();
            Integer lotSize = security.getLotSize();

            // 先计算目标交易手数，再由交易手数转换为目标数量
            int targetLot = (int)Math.round(targetValue / price / lotSize);
            targetQuantityMap.put(symbol, targetLot * lotSize);
        }

        // 计算剩余资金
        double remainingFunds = totalValue;
        for (String symbol : targetSymbols) {
            Integer targetQuantity = targetQuantityMap.get(symbol);
            Security security = securityMap.get(symbol);
            remainingFunds -= targetQuantity * security.getPrice();
        }
        System.out.println("首次分配结果 => remainingFunds: " + remainingFunds + ", targetQuantityMap: " + JSON.toJSONString(targetQuantityMap));

        // 二次分配剩余资金
        List<Security> sortedTargetSecurities = targetSymbols.stream()
                .map(securityMap::get)
                .sorted(Comparator.comparing(Security::getPrice))
                .toList();
        boolean canAdjust;
        do {
            canAdjust = false;

            // 如果有余钱，尝试继续买入
            if (remainingFunds > 0) {
                for (Security security : sortedTargetSecurities) {
                    String symbol = security.getSymbol();
                    Double price = security.getPrice();
                    Integer lotSize = security.getLotSize();

                    Integer targetQuantity = targetQuantityMap.get(symbol);
                    if (targetQuantity != 0) {
                        // 如果剩余资金不足以购买任何额外的股份，则停止循环
                        if (remainingFunds - price * lotSize <= 0) break;

                        targetQuantityMap.put(symbol, targetQuantity + lotSize);
                        remainingFunds -= price * lotSize;
                        canAdjust = true;
                    }
                }
            } else if (remainingFunds < 0) {
                for (Security security : sortedTargetSecurities) {
                    String symbol = security.getSymbol();
                    Double price = security.getPrice();
                    Integer lotSize = security.getLotSize();

                    Integer targetQuantity = targetQuantityMap.get(symbol);
                    if (targetQuantity != 0) {
                        // 如果剩余资金大于0，则停止循环
                        if (remainingFunds > 0) break;

                        targetQuantityMap.put(symbol, targetQuantity - lotSize);
                        remainingFunds += price * lotSize;
                        canAdjust = true;
                    }
                }
            }
        } while (canAdjust); // 继续循环直到无法调整
        System.out.println("二次分配结果 => remainingFunds: " + remainingFunds + ", targetQuantityMap: " + JSON.toJSONString(targetQuantityMap));

        // 根据目标持仓，计算需要买入和卖出的标的
        List<TradingPlan> tradingPlans = new ArrayList<>();
        for (String symbol : symbols) {
            Security security = securityMap.get(symbol);
            Integer holdingQuantity = holdingQuantityMap.getOrDefault(symbol, 0);
            Integer targetQuantity = targetQuantityMap.getOrDefault(symbol, 0);
            System.out.println("security: " + security + ", holdingQuantity: " + holdingQuantity + ", targetQuantity: " + targetQuantity);

            int changeQuantity = targetQuantity - holdingQuantity;
            if (changeQuantity != 0) {
                tradingPlans.add(new TradingPlan(security, changeQuantity));
            }
        }
        return tradingPlans;
    }

    @Data
    public static class Context implements TradingStrategy.Context {
        /**
         * 账户余额
         */
        private final Double balance;
        /**
         * 标的持有数量映射 Map<标的代码, 持有数量>
         */
        private final Map<String, Integer> holdingQuantityMap;
        /**
         * 目标标的池
         */
        private final Set<String> targetSymbols;

        public Context(Account account, Set<String> targetSymbols) {
            this.balance = account.getBalance();
            this.holdingQuantityMap = account.getPositionMap().values().stream().collect(Collectors.toMap(Position::getSymbol, Position::getQuantity));
            this.targetSymbols = targetSymbols;
        }
    }
}
