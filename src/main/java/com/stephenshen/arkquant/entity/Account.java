package com.stephenshen.arkquant.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 账户信息
 *
 * @author stephenshen
 * @date 2024/8/28 07:08:44
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class Account {
    // 余额
    private Double balance;
    // 持仓信息 Map<标的代码, 标的持仓>
    private Map<String, Position> positionMap;

    public Account(Double balance, List<Position> positions) {
        this.balance = balance;
        this.positionMap = positions.stream().collect(Collectors.toMap(Position::getSymbol, Function.identity()));
    }

    /**
     * 账户价值
     * @return
     */
    public double totalValue() {
        return positionMap.values().stream().map(Position::getValue).reduce(balance, Double::sum);
    }
}
