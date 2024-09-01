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
    public TradingPlan makeTradingPlan(Context context) {
        Account account = context.getAccount();
        Set<String> targetSymbols = context.getTargetSymbols();

        Map<String, Position> currentPositionMap = account.getPositionMap();

        // 获取标的信息
        Set<String> symbols = Sets.union(currentPositionMap.keySet(), targetSymbols);
        Map<String, Security> securityMap = securityRepository.getSecurityMap(symbols);

        // 账户市值
        double currentTotalValue = account.totalValue();

        // 计算标的平均价值
        double avgValue = currentTotalValue / targetSymbols.size();
        System.out.println("avgValue => " + avgValue);

        // 计算目标持仓
        List<Position> targetPositions = new ArrayList<>();
        for (String symbol : symbols) {
            Security security = securityMap.get(symbol);
            Double price = security.getPrice();
            Integer lotSize = security.getLotSize();

            // 计算目标数量: 在标的池内，则取平均价值除以标的价格向上取整，否则为0
            int targetQuantity = targetSymbols.contains(symbol) ? roundUpToNextLot(avgValue / price, lotSize) : 0;

            Position position = new Position(symbol, security.getName(), price, targetQuantity);
            targetPositions.add(position);
        }

        // 调整目标持仓: 按持仓价值从高到低依次调整目标持仓数量，直到调整后目标持仓总价值小于账户价值，否则继续调整
        targetPositions.sort(Comparator.comparing(Position::getValue, Comparator.reverseOrder()));
        System.out.println("调整前 targetPositions => " + JSON.toJSONString(targetPositions));

        double targetTotalValue = targetPositions.stream().map(Position::getValue).mapToDouble(Double::doubleValue).sum();
        for (Position targetPosition : targetPositions) {
            if (targetTotalValue < currentTotalValue) break;
            Security security = securityMap.get(targetPosition.getSymbol());
            targetPosition.updateQuantity(targetPosition.getQuantity() - security.getLotSize());
        }

        System.out.println("调整后 targetPositions => " + JSON.toJSONString(targetPositions));

        // 根据目标持仓，计算需要买入和卖出的标的
        TradingPlan tradingPlan = new TradingPlan();
        for (Position targetPosition : targetPositions) {
            String symbol = targetPosition.getSymbol();
            Position currentPosition = currentPositionMap.get(symbol);

            Integer targetPositionQuantity = targetPosition.getQuantity();
            Integer currentPositionQuantity = currentPosition != null ? currentPosition.getQuantity() : 0;

            if (currentPositionQuantity < targetPositionQuantity) {
                tradingPlan.addBuy(symbol, targetPositionQuantity - currentPositionQuantity);
            } else if (currentPositionQuantity > targetPositionQuantity) {
                tradingPlan.addSell(symbol, currentPositionQuantity - targetPositionQuantity);
            }
        }
        return tradingPlan;
    }

    /**
     * 计算需要多少手才能达到或超过目标数量，然后向上取整
     * @param targetQuantity
     * @param lotSize
     * @return
     */
    private int roundUpToNextLot(double targetQuantity, Integer lotSize) {
        return (int) Math.ceil(targetQuantity / lotSize) * lotSize;
    }

    @Data
    public static class Context implements TradingStrategy.Context {
        /**
         * 账户信息
         */
        private final Account account;
        /**
         * 标的池
         */
        private final Set<String> targetSymbols;
    }
}
